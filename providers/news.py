"""
Genel haber toplama — Google News RSS.

KAP birincil kaynaktır; bu modül yedek/tamamlayıcıdır. Haber metinleri
olduğu gibi kopyalanmaz: başlık temizlenir ve RSS özetinden kural tabanlı
2-3 cümlelik kısa bir özet üretilir, kaynağa link verilir.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from logging_setup import get_logger
from providers.base import FetchResult, NewsItem, news_limiter, retry
from security import sanitize_url

log = get_logger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=tr&gl=TR&ceid=TR:tr"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Dış kaynaktan gelen metinler için üst sınırlar. Aşırı uzun bir başlık
# e-postayı ve arayüzü bozabilir; kaynak bizim kontrolümüzde değil.
MAX_TITLE_CHARS = 300
MAX_SOURCE_CHARS = 80

# Özet üretiminde atılacak kalıplar (RSS'lerde sık görülen gürültü)
_NOISE_PATTERNS = [
    re.compile(r"<[^>]+>"),                       # HTML etiketleri
    re.compile(r"&nbsp;?", re.IGNORECASE),
    re.compile(r"https?://\S+"),                  # çıplak linkler
    re.compile(r"\s{2,}"),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÇĞİÖŞÜ0-9])")


def strip_html(text: str) -> str:
    """HTML etiketlerini ve varlıklarını temizler, boşlukları normalize eder."""
    if not text:
        return ""
    cleaned = html.unescape(str(text))
    cleaned = re.sub(r"<br\s*/?>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</p>", " ", cleaned, flags=re.IGNORECASE)
    for pattern in _NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return cleaned.strip()


def summarize(text: str, max_sentences: int = 2, max_chars: int = 300) -> str:
    """
    Kural tabanlı kısa özet.

    Metni temizler, ilk `max_sentences` cümleyi alır ve `max_chars` sınırına
    göre kırpar. LLM kullanmaz — bedava, offline ve deterministiktir.
    """
    cleaned = strip_html(text)
    if not cleaned:
        return ""

    sentences = _SENTENCE_SPLIT.split(cleaned)
    summary = " ".join(s.strip() for s in sentences[:max_sentences]).strip()

    if len(summary) > max_chars:
        # Kelime ortasında kesmemek için son boşluktan kırp.
        cut = summary[:max_chars].rsplit(" ", 1)[0]
        summary = cut.rstrip(",;:.") + "…"

    return summary


def clean_title(title: str, source_name: str | None = None) -> str:
    """
    Google News başlıklarındaki " - Kaynak Adı" ekini temizler.
    """
    cleaned = strip_html(title)
    if source_name and cleaned.endswith(f" - {source_name}"):
        cleaned = cleaned[: -len(f" - {source_name}")]
    return cleaned.strip()


def _normalize_for_compare(text: str) -> str:
    """Karşılaştırma için metni sadeleştirir: küçük harf, yalnız harf/rakam."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def is_redundant_summary(summary: str, title: str, source_name: str = "") -> bool:
    """
    Özet, başlığın tekrarından ibaret mi?

    Google News RSS'in `summary` alanı çoğu zaman yalnızca başlığı içeren bir
    `<a>` bağlantısıdır. HTML temizlendikten sonra geriye "Başlık Kaynak Adı"
    kalır — bu, başlığın hemen altında ikinci kez göstermeye değmez.
    """
    if not summary:
        return True

    norm_summary = _normalize_for_compare(summary)
    norm_title = _normalize_for_compare(title)
    if not norm_summary or not norm_title:
        return True

    # Özet başlığın içinde ya da başlık özetin içindeyse tekrar sayılır.
    if norm_summary == norm_title:
        return True

    # Kaynak adı çıkarıldığında başlıkla aynı kalıyorsa da tekrar.
    if source_name:
        without_source = norm_summary.replace(_normalize_for_compare(source_name), "")
        if without_source == norm_title:
            return True

    # Özet, başlıktan anlamlı biçimde uzun değilse yeni bilgi taşımıyordur.
    if norm_title in norm_summary and len(norm_summary) < len(norm_title) * 1.4:
        return True

    return False


def build_query(symbol: str, company_name: str | None = None) -> str:
    """
    Arama sorgusunu kurar.

    Şirket adı biliniyorsa `"THYAO" OR "Türk Hava Yolları"` şeklinde iki
    terimli sorgu; bilinmiyorsa `"THYAO" hisse` ile gürültü azaltılır.
    """
    if company_name:
        terms = f'"{symbol}" OR "{company_name}"'
    else:
        terms = f'"{symbol}" hisse'
    return urllib.parse.quote(terms)


# RSS yanıtı için üst sınır. Kötü niyetli/bozuk bir sunucunun sonsuz akışla
# belleği doldurmasını engeller.
MAX_FEED_BYTES = 4 * 1024 * 1024  # 4 MB


@retry(times=2, delay=1.5)
def _parse_feed(url: str, timeout: int | None = None):
    """
    RSS akışını indirir ve ayrıştırır.

    Güvenlik notu (O-2): `feedparser.parse(url)` çağrısı akışı kendisi indirir
    ve **hiçbir timeout uygulamaz** — yanıt vermeyen bir sunucu thread'i
    süresiz bloklar ve zamanlayıcıyı kalıcı olarak dondurur. Bu yüzden indirme
    `requests` ile timeout'lu yapılır, feedparser'a yalnızca baytlar verilir.
    """
    import feedparser

    from config import config

    news_limiter.wait()  # D-1: kaynağa nazik davran

    response = requests.get(
        url,
        timeout=timeout or config.request_timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
        stream=True,
    )
    response.raise_for_status()

    # Yanıtı parça parça oku ve boyut sınırını aş(ma)yı kontrol et.
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=16 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_FEED_BYTES:
            response.close()
            raise ValueError(f"RSS yanıtı çok büyük (>{MAX_FEED_BYTES // 1024 // 1024} MB)")
        chunks.append(chunk)
    response.close()

    feed = feedparser.parse(b"".join(chunks))
    # feedparser hata durumunda exception atmaz; bozo bayrağını kontrol et.
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ConnectionError(f"RSS alınamadı: {getattr(feed, 'bozo_exception', 'bilinmeyen hata')}")
    return feed


def _entry_datetime(entry) -> datetime | None:
    """RSS girdisinin yayın zamanını UTC datetime olarak döndürür."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                import calendar

                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            except Exception:
                continue
    return None


def get_news(
    symbol: str,
    company_name: str | None = None,
    lookback_hours: int | None = None,
    max_items: int | None = None,
) -> FetchResult[list[NewsItem]]:
    """
    Bir hisse için son haberleri döndürür.

    Zaman penceresi dışındaki ve alakasız (ne kod ne şirket adı geçen)
    haberler elenir. Hata durumunda failure döner, süreç durmaz.
    """
    from config import config

    hours = lookback_hours if lookback_hours is not None else config.news_lookback_hours
    limit = max_items if max_items is not None else config.news_max_items
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    url = GOOGLE_NEWS_RSS.format(query=build_query(symbol, company_name))

    try:
        feed = _parse_feed(url)
    except Exception as exc:
        log.warning("%s için haber alınamadı: %s", symbol, exc)
        return FetchResult.failure(f"Google News: {type(exc).__name__}: {exc}")

    items: list[NewsItem] = []
    for entry in getattr(feed, "entries", []):
        published = _entry_datetime(entry)
        if published and published < cutoff:
            continue

        source_name = ""
        source_obj = getattr(entry, "source", None)
        if source_obj is not None:
            source_name = getattr(source_obj, "title", "") or ""

        title = clean_title(getattr(entry, "title", ""), source_name)[:MAX_TITLE_CHARS]
        if not title:
            continue

        # Alaka kontrolü: kod ya da şirket adı başlıkta/özette geçmeli.
        haystack = f"{title} {getattr(entry, 'summary', '')}".upper()
        needles = [symbol.upper()]
        if company_name:
            needles.append(company_name.upper())
        if not any(n in haystack for n in needles):
            continue

        # Güvenlik (O-3): URL şeması doğrulanır. `javascript:` gibi şemalar
        # önizleme sayfasında tıklandığında uygulamanın kaynağında kod
        # çalıştırabilir; güvenli olmayan URL'ler boşaltılır ve haber
        # bağlantısız düz metin olarak gösterilir.
        raw_url = getattr(entry, "link", "") or ""
        safe = sanitize_url(raw_url)
        if raw_url and not safe:
            log.warning(
                "%s: güvenli olmayan şemaya sahip haber URL'i atlandı (%.60s)", symbol, raw_url
            )

        # Özet yalnızca başlığın tekrarıysa gösterme — e-postada aynı cümleyi
        # üst üste iki kez okumak bilgi katmıyor.
        clean_source = strip_html(source_name)[:MAX_SOURCE_CHARS] or "Google News"
        raw_summary = summarize(getattr(entry, "summary", ""))
        summary = "" if is_redundant_summary(raw_summary, title, clean_source) else raw_summary

        items.append(
            NewsItem(
                title=title,
                url=safe,
                source=clean_source,
                published=published,
                summary=summary,
                kind="news",
            )
        )
        if len(items) >= limit:
            break

    log.info("%s için %d haber bulundu", symbol, len(items))
    return FetchResult.success(items, source="Google News")
