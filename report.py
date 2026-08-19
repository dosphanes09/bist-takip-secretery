"""
Rapor üretimi — tüm veri kaynaklarını bir araya getiren orkestrasyon katmanı.

Her hisse için fiyat, KAP bildirimleri, haberler ve analist hedefleri
paralel olarak toplanır. Bir kaynak düşerse yalnızca o bölüm boş kalır;
hisse de, rapor da düşmez.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import db
from config import config
from formatting import fmt_pct, fmt_pct_plain, fmt_tl
from logging_setup import get_logger
from providers import kap as kap_provider
from providers import news as news_provider
from providers import prices as price_provider
from providers import symbols as symbol_provider
from providers.analysts import get_targets
from providers.base import StockReport

log = get_logger(__name__)

# Yapılandırmadan gelse bile aşılmayacak eşzamanlılık sınırı (D-1).
MAX_ALLOWED_WORKERS = 8

# Hata metninde görünebilecek dosya yollarını yakalayan kalıplar (O-5).
_PATH_PATTERNS = (
    re.compile(r"(?:/[\w.\-]+){2,}"),          # POSIX yolları: /home/kullanici/proje/...
    re.compile(r"[A-Za-z]:\\(?:[\w.\- ]+\\?)+"),  # Windows yolları: C:\Users\...
)

MAX_ERROR_CHARS = 180


def _safe_error_text(exc: Exception | str) -> str:
    """
    Hata metnini rapora/e-postaya konmadan önce temizler (O-5).

    Ham istisna metinleri dosya yolları, kullanıcı adı ve dahili URL'ler
    içerebilir. Rapor e-postası iletilebilir bir belgedir; bu bilgilerin
    içinde taşınmasını istemiyoruz. Tam metin log dosyasında kalır.
    """
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for pattern in _PATH_PATTERNS:
        text = pattern.sub("<yol>", text)
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS].rstrip() + "…"
    return text


@dataclass
class Report:
    """Bir çalıştırmanın tüm çıktısı."""

    generated_at: datetime
    stocks: list[StockReport] = field(default_factory=list)
    global_errors: list[str] = field(default_factory=list)

    @property
    def failures(self) -> int:
        """Fiyatı alınamayan hisse sayısı."""
        return sum(1 for s in self.stocks if not s.has_price)

    @property
    def alert_count(self) -> int:
        return sum(len(s.alerts) for s in self.stocks)

    @property
    def gainers(self) -> list[StockReport]:
        """Yükselenler, en çok yükselenden başlayarak."""
        items = [s for s in self.stocks if s.price and (s.price.change_percent or 0) > 0]
        return sorted(items, key=lambda s: s.price.change_percent or 0, reverse=True)

    @property
    def losers(self) -> list[StockReport]:
        """Düşenler, en çok düşenden başlayarak."""
        items = [s for s in self.stocks if s.price and (s.price.change_percent or 0) < 0]
        return sorted(items, key=lambda s: s.price.change_percent or 0)

    @property
    def total_news(self) -> int:
        return sum(len(s.all_items) for s in self.stocks)


# ---------------------------------------------------------------------------
# Alarm mantığı
# ---------------------------------------------------------------------------

def _evaluate_alerts(report: StockReport) -> list[str]:
    """
    Hisse için tetiklenen alarm mesajlarını üretir.

    İki tür alarm var:
      1. Yüzde değişim eşiği (hisse bazlı `alert_pct`, yoksa varsayılan)
      2. Hedef fiyat geçişi (`target_price`)
    """
    alerts: list[str] = []
    price = report.price
    if price is None or price.last is None:
        return alerts

    # 1) Günlük değişim eşiği
    threshold = report.alert_pct if report.alert_pct is not None else config.default_alert_pct
    change_pct = price.change_percent
    if threshold and change_pct is not None and abs(change_pct) >= abs(threshold):
        direction = "yükseldi" if change_pct > 0 else "düştü"
        alerts.append(
            f"Günlük değişim eşiği aşıldı: {fmt_pct(change_pct)} "
            f"({direction}, eşik ±{fmt_pct_plain(threshold)})"
        )

    # 2) Hedef fiyat
    if report.target_price:
        target = report.target_price
        if price.last >= target:
            alerts.append(
                f"Hedef fiyata ulaşıldı/aşıldı: {fmt_tl(price.last)} ≥ {fmt_tl(target)}"
            )
        else:
            remaining = (target - price.last) / price.last * 100.0
            if remaining <= 3.0:
                alerts.append(
                    f"Hedef fiyata çok yakın: {fmt_tl(price.last)} "
                    f"(hedefe {fmt_pct_plain(remaining)} kaldı)"
                )

    return alerts


# ---------------------------------------------------------------------------
# Tek hisse toplama
# ---------------------------------------------------------------------------

def build_stock_report(row: dict[str, Any], skip_seen_news: bool = True) -> StockReport:
    """
    Tek bir hisse için tüm verileri toplar.

    Bu fonksiyon asla exception fırlatmaz — her alt çağrı kendi hatasını
    ilgili `*_error` alanına yazar.
    """
    symbol = row["symbol"]
    name = row.get("name") or symbol_provider.resolve_name(symbol)

    stock = StockReport(
        symbol=symbol,
        name=name,
        target_price=row.get("target_price"),
        alert_pct=row.get("alert_pct"),
    )

    # --- Fiyat ---
    price_result = price_provider.get_price(symbol)
    if price_result.ok:
        stock.price = price_result.data
    else:
        stock.price_error = _safe_error_text(price_result.error or "")

    # --- KAP bildirimleri ---
    kap_result = kap_provider.get_disclosures(symbol)
    if kap_result.ok:
        stock.kap_items = kap_result.data or []
    else:
        stock.kap_error = _safe_error_text(kap_result.error or "")

    # --- Genel haberler ---
    if config.news_enabled:
        news_result = news_provider.get_news(symbol, company_name=name)
        if news_result.ok:
            stock.news_items = news_result.data or []
        else:
            stock.news_error = _safe_error_text(news_result.error or "")

    # --- Daha önce gönderilenleri ele ---
    if skip_seen_news:
        try:
            all_keys = [item.key for item in stock.all_items]
            unseen = db.filter_unseen(symbol, all_keys)
            stock.kap_items = [i for i in stock.kap_items if i.key in unseen]
            stock.news_items = [i for i in stock.news_items if i.key in unseen]
        except Exception as exc:
            # Tekrar filtresi çökerse haberleri göstermeye devam et.
            log.warning("%s için tekrar filtresi uygulanamadı: %s", symbol, exc)

    # --- Analist hedefleri (opsiyonel) ---
    if config.analysts_enabled:
        current = stock.price.last if stock.price else None
        analyst_result = get_targets(symbol, current_price=current)
        if analyst_result.ok:
            stock.analyst = analyst_result.data
        else:
            stock.analyst_error = _safe_error_text(analyst_result.error or "")

    # --- Alarmlar ---
    stock.alerts = _evaluate_alerts(stock)

    return stock


# ---------------------------------------------------------------------------
# Tam rapor
# ---------------------------------------------------------------------------

def mark_report_sent(report: "Report") -> None:
    """
    Rapordaki haber ve bildirimleri "gönderildi" olarak işaretler.

    ÖNEMLİ: Bu fonksiyon yalnızca e-posta **başarıyla gönderildikten sonra**
    çağrılmalıdır. Daha önce işaretleme rapor üretilirken yapılıyordu; gönderim
    çökerse haberler kalıcı olarak kayboluyor ve bir daha hiçbir e-postada
    görünmüyordu (canlı kullanımda yaşandı).
    """
    for stock in report.stocks:
        try:
            db.mark_sent(stock.symbol, [i.key for i in stock.all_items])
        except Exception as exc:
            log.warning("%s için gönderim işareti kaydedilemedi: %s", stock.symbol, exc)


def build_report(
    symbols: list[str] | None = None,
    skip_seen_news: bool = True,
) -> Report:
    """
    Takip listesindeki (ya da verilen) hisseler için tam raporu üretir.

    Args:
        symbols:        Sadece bu kodlar için üret. None ise DB'deki aktif liste.
        skip_seen_news: Daha önce gönderilmiş haberleri ele.

    Not: Haberleri "gönderildi" olarak işaretlemek bu fonksiyonun işi değildir —
    gönderim başarılı olduktan sonra `mark_report_sent()` çağrılır.
    """
    kap_provider.clear_cache()  # her çalıştırmada taze KAP verisi

    if symbols:
        rows = []
        for sym in symbols:
            existing = db.get_stock(sym)
            rows.append(existing or {"symbol": db.normalize_symbol(sym)})
    else:
        rows = db.list_stocks(only_active=True)

    report = Report(generated_at=datetime.now())

    if not rows:
        report.global_errors.append(
            "Takip listesi boş. Web arayüzünden ya da --add ile hisse ekleyin."
        )
        return report

    log.info("%d hisse için rapor üretiliyor…", len(rows))

    # Hisseleri paralel işle; ağ beklemeleri üst üste binmesin.
    # `max_workers` üst sınırla kapatılır (D-1): yapılandırma hatası
    # kaynaklara karşı istek patlamasına dönüşmesin.
    workers = max(1, min(config.max_workers, MAX_ALLOWED_WORKERS, len(rows)))
    results: dict[str, StockReport] = {}

    # O-2: Havuzun tamamı için üst süre sınırı. Bir sağlayıcı asılırsa
    # (ör. yanıt vermeyen bir sunucu) zamanlayıcı thread'i süresiz donmasın;
    # süre dolunca elde olan sonuçlarla rapor üretilir.
    deadline = config.run_timeout_seconds

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(build_stock_report, row, skip_seen_news): row["symbol"] for row in rows
        }
        try:
            for future in as_completed(futures, timeout=deadline):
                symbol = futures[future]
                try:
                    results[symbol] = future.result()
                except Exception as exc:
                    # Teorik olarak buraya düşmemeli; yine de raporu ayakta tut.
                    log.exception("%s işlenirken beklenmeyen hata", symbol)
                    failed = StockReport(symbol=symbol)
                    failed.price_error = _safe_error_text(exc)
                    results[symbol] = failed
        except FuturesTimeout:
            unfinished = [sym for fut, sym in futures.items() if not fut.done()]
            log.error(
                "Rapor süre sınırını aştı (%ds). Tamamlanmayan hisseler: %s",
                deadline, ", ".join(unfinished) or "—",
            )
            report.global_errors.append(
                f"Veri toplama {deadline} saniyelik süre sınırını aştı; "
                f"{len(unfinished)} hisse tamamlanamadı."
            )
            for future, symbol in futures.items():
                if not future.done():
                    future.cancel()
                    timed_out = StockReport(symbol=symbol)
                    timed_out.price_error = "Veri kaynağı zamanında yanıt vermedi."
                    results.setdefault(symbol, timed_out)

    # DB sırasını koru (alfabetik)
    report.stocks = [results[row["symbol"]] for row in rows if row["symbol"] in results]

    log.info(
        "Rapor hazır: %d hisse, %d başarısız, %d haber, %d alarm",
        len(report.stocks), report.failures, report.total_news, report.alert_count,
    )
    return report
