"""
KAP (Kamuyu Aydınlatma Platformu) bildirimleri.

Birincil kaynak: KAP'ın resmî sorgu API'si
    POST https://www.kap.org.tr/tr/api/disclosure/members/byCriteria
Tek çağrıda tarih aralığındaki TÜM bildirimler gelir; takip listesindeki
hisseler bu tek yanıt üzerinden filtrelenir (hisse başına ayrı istek yok).

Yedek kaynak: borsapy'nin `Ticker.news` özelliği (KAP sayfasını kazır).
Resmî API erişilemezse devreye girer.
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime, timedelta
from typing import Any

import requests

from logging_setup import get_logger
from providers.base import FetchResult, NewsItem, kap_limiter, retry
from security import sanitize_url

log = get_logger(__name__)

KAP_API_URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
KAP_DETAIL_URL = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{}"
KAP_BILDIRIM_URL = "https://www.kap.org.tr/tr/Bildirim/{}"
KAP_SEARCH_URL = "https://www.kap.org.tr/tr/bildirim-sorgu"

# Dış kaynaktan gelen metinler için üst sınır
MAX_TITLE_CHARS = 300

# Yanıt boyutu üst sınırı — bozuk/kötü niyetli bir yanıtın belleği
# doldurmasını engeller.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MB

_HEADERS = {
    "Referer": "https://www.kap.org.tr/tr/bildirim-sorgu",
    "Origin": "https://www.kap.org.tr",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

# Rapor çalıştırması boyunca API yanıtını tek seferlik önbelleğe alır.
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "rows": None, "error": None}


# ---------------------------------------------------------------------------
# Resmî KAP API
# ---------------------------------------------------------------------------

@retry(times=2, delay=2.0)
def _fetch_all_disclosures(from_date: date, to_date: date, timeout: int) -> list[dict]:
    """Tarih aralığındaki tüm KAP bildirimlerini indirir (max 2000 kayıt)."""
    payload = {
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "mkkMemberOidList": [],
        "subjectList": [],
    }
    kap_limiter.wait()  # D-1: kaynağa nazik davran
    response = requests.post(KAP_API_URL, json=payload, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()

    # Yanıt boyutunu sınırla — sunucu bizim kontrolümüzde değil.
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ValueError(f"KAP yanıtı çok büyük ({declared} bayt)")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("KAP yanıtı boyut sınırını aştı")

    data = response.json()
    if not isinstance(data, list):
        raise ValueError(f"beklenmeyen yanıt tipi: {type(data).__name__}")
    return data


def load_disclosures(lookback_days: int | None = None, timeout: int | None = None) -> list[dict]:
    """
    Bildirimleri getirir ve süreç içinde önbelleğe alır.
    Aynı çalıştırmada 30 hisse için 30 kez indirmemek içindir.

    Kilit indirme boyunca tutulur: hisseler paralel işlendiği için aksi halde
    tüm thread'ler önbelleği aynı anda ıskalayıp aynı isteği tekrar tekrar
    gönderir. İlk thread indirirken diğerleri bekler, sonra önbelleği kullanır.
    """
    from config import config

    days = lookback_days if lookback_days is not None else config.kap_lookback_days
    tmo = timeout or config.request_timeout

    to_date = date.today()
    from_date = to_date - timedelta(days=max(0, days))
    key = f"{from_date}:{to_date}"

    with _cache_lock:
        # Çift kontrol: kilidi beklerken başka bir thread doldurmuş olabilir.
        if _cache["key"] == key and _cache["rows"] is not None:
            return _cache["rows"]

        # Aynı çalıştırmada tekrar tekrar denenip her seferinde
        # zaman aşımı beklenmesin diye hata da önbelleğe alınır.
        if _cache["key"] == key and _cache["error"] is not None:
            raise _cache["error"]

        try:
            rows = _fetch_all_disclosures(from_date, to_date, tmo)
        except Exception as exc:
            _cache["key"] = key
            _cache["error"] = exc
            raise

        _cache["key"] = key
        _cache["rows"] = rows
        _cache["error"] = None

    log.info("KAP: %s–%s aralığında %d bildirim indirildi", from_date, to_date, len(rows))
    return rows


def clear_cache() -> None:
    """Zamanlanmış her yeni çalıştırmada taze veri almak için önbelleği boşaltır."""
    with _cache_lock:
        _cache["key"] = None
        _cache["rows"] = None
        _cache["error"] = None


def _related_symbols(row: dict) -> set[str]:
    """
    Bir bildirimin ilgili hisse kodlarını çıkarır.
    `relatedStocks` alanı sürüme göre liste ya da virgüllü metin olabilir.
    """
    raw = row.get("relatedStocks") or row.get("stockCodes") or ""
    if isinstance(raw, list):
        parts = [str(p) for p in raw]
    else:
        parts = re.split(r"[,;/\s]+", str(raw))
    return {p.strip().upper() for p in parts if p and p.strip()}


def _parse_kap_date(value: Any) -> datetime | None:
    """KAP tarih formatlarını datetime'a çevirir."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _safe_disclosure_index(value: Any) -> str:
    """
    Bildirim numarasını doğrular.

    Değer KAP yanıtından geliyor ve doğrudan URL'e gömülüyor. Yalnızca
    rakamlara izin vererek hem URL enjeksiyonunu hem de `_safe_disclosure_index`
    ile kurulan istek yollarının manipüle edilmesini engelliyoruz.
    """
    text = str(value or "").strip()
    return text if text.isdigit() and len(text) <= 20 else ""


def _row_to_item(row: dict) -> NewsItem:
    """API satırını NewsItem'a dönüştürür."""
    index = _safe_disclosure_index(row.get("disclosureIndex") or row.get("id"))

    # Dış kaynaktan gelen metinler; uzunluk sınırlanır.
    subject = (row.get("subject") or "").strip()[:MAX_TITLE_CHARS]
    title = subject or (row.get("kapTitle") or "").strip()[:MAX_TITLE_CHARS] or "KAP Bildirimi"

    disclosure_class = (row.get("disclosureClass") or "").strip()[:80]
    company = (row.get("kapTitle") or "").strip()[:120]

    summary_bits = [b for b in (company, disclosure_class) if b]
    summary = " · ".join(summary_bits)

    url = KAP_BILDIRIM_URL.format(index) if index else KAP_SEARCH_URL

    return NewsItem(
        title=title,
        url=sanitize_url(url),
        source="KAP",
        published=_parse_kap_date(row.get("publishDate")),
        summary=summary,
        kind="kap",
    )


# ---------------------------------------------------------------------------
# borsapy yedeği
# ---------------------------------------------------------------------------

def _fetch_via_borsapy(symbol: str, limit: int) -> list[NewsItem]:
    """
    borsapy'nin KAP kazıyıcısı ile bildirimleri alır (yedek yol).

    Önemli: borsapy şirket listesini indiremediğinde exception atmak yerine
    sessizce boş DataFrame döndürür. Bu yola yalnızca resmî API zaten
    başarısız olduğunda gelindiği için, boş sonucu "bugün bildirim yok"
    diye kabul etmek yanıltıcı olur — e-posta ağ hatasını sessizce gizler.
    Bu yüzden boş sonuç açıkça hata sayılır.
    """
    import borsapy as bp

    df = bp.Ticker(symbol).news
    if df is None or getattr(df, "empty", True):
        raise ValueError(
            "borsapy boş sonuç döndürdü — bildirim olmadığı doğrulanamadı "
            "(muhtemelen KAP'a erişilemiyor)"
        )

    items: list[NewsItem] = []
    for _, row in df.head(limit).iterrows():
        items.append(
            NewsItem(
                title=str(row.get("Title") or "KAP Bildirimi").strip()[:MAX_TITLE_CHARS],
                url=sanitize_url(str(row.get("URL") or "")),
                source="KAP",
                published=_parse_kap_date(row.get("Date")),
                summary="",
                kind="kap",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Genel arayüz
# ---------------------------------------------------------------------------

def get_disclosures(symbol: str, limit: int | None = None) -> FetchResult[list[NewsItem]]:
    """
    Bir hisse için son KAP bildirimlerini döndürür.

    Önce resmî API'den (önbellekli toplu indirme) filtreler; API erişilemezse
    borsapy'ye düşer. İkisi de olmazsa failure döner — çağıran taraf raporu
    bu hisse için "KAP verisi alınamadı" notuyla sürdürür.
    """
    from config import config

    max_items = limit if limit is not None else config.kap_max_items
    sym = symbol.upper()
    errors: list[str] = []

    # 1) Resmî API
    try:
        rows = load_disclosures()
        matched = [r for r in rows if sym in _related_symbols(r)]
        matched.sort(key=lambda r: _parse_kap_date(r.get("publishDate")) or datetime.min, reverse=True)
        items = [_row_to_item(r) for r in matched[:max_items]]
        log.info("%s için KAP API'den %d bildirim bulundu", sym, len(items))
        return FetchResult.success(items, source="KAP API")
    except Exception as exc:
        errors.append(f"KAP API: {type(exc).__name__}: {exc}")
        log.warning("%s için KAP API başarısız: %s", sym, exc)

    # 2) borsapy yedeği
    try:
        items = _fetch_via_borsapy(sym, max_items)
        log.info("%s için borsapy yedeğinden %d bildirim alındı", sym, len(items))
        return FetchResult.success(items, source="KAP (borsapy)")
    except Exception as exc:
        errors.append(f"borsapy: {type(exc).__name__}: {exc}")
        log.warning("%s için borsapy KAP yedeği de başarısız: %s", sym, exc)

    return FetchResult.failure(" | ".join(errors))


def get_disclosure_summary(disclosure_index: str | int, timeout: int | None = None) -> str:
    """
    Bir bildirimin gövdesinden kısa bir özet çıkarır (opsiyonel zenginleştirme).

    Tam metni kopyalamaz; HTML etiketlerini temizleyip ilk birkaç cümleyi alır.
    Hata durumunda boş string döner — asla exception fırlatmaz.
    """
    from config import config
    from providers.news import summarize

    # Numara doğrulanmadan URL'e gömülmez.
    index = _safe_disclosure_index(disclosure_index)
    if not index:
        log.debug("Geçersiz KAP bildirim numarası atlandı: %r", disclosure_index)
        return ""

    try:
        response = requests.get(
            KAP_DETAIL_URL.format(index),
            headers={**_HEADERS, "Referer": KAP_BILDIRIM_URL.format(index)},
            timeout=timeout or config.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            payload = payload[0]
        body = (payload or {}).get("disclosureBody") or ""
        return summarize(body, max_sentences=2)
    except Exception as exc:
        log.debug("KAP bildirim detayı alınamadı (%s): %s", disclosure_index, exc)
        return ""
