"""
Analist hedef fiyatları ve tavsiye özeti.

Kaynak: borsapy — `Ticker.analyst_price_targets` (hedeffiyat.com.tr) ve
`Ticker.recommendations` (İş Yatırım).

Bu bölüm tamamen opsiyoneldir: veri bulunamazsa rapor bloklanmaz, ilgili
kutu e-postada gösterilmez.
"""

from __future__ import annotations

from logging_setup import get_logger
from providers.base import AnalystTarget, FetchResult, safe_float

log = get_logger(__name__)

# recommendations_summary anahtarlarının Türkçe etiketleri
RECOMMENDATION_LABELS = {
    "strongBuy": "Güçlü Al",
    "buy": "Al",
    "hold": "Tut",
    "sell": "Sat",
    "strongSell": "Güçlü Sat",
}


def get_targets(symbol: str, current_price: float | None = None) -> FetchResult[AnalystTarget]:
    """
    Hisse için analist hedef fiyat özetini döndürür.

    İki ayrı kaynak denenir ve elde edilenler birleştirilir; biri düşse bile
    diğerinin verisi kullanılır. Hiçbiri gelmezse failure döner.
    """
    sym = symbol.upper()
    target = AnalystTarget(symbol=sym, current=current_price, source="borsapy")
    errors: list[str] = []

    try:
        import borsapy as bp
    except Exception as exc:
        return FetchResult.failure(f"borsapy yüklenemedi: {exc}")

    ticker = bp.Ticker(sym)

    # 1) Hedef fiyat aralığı (low / high / mean / median / analist sayısı)
    try:
        raw = ticker.analyst_price_targets or {}
        target.current = safe_float(raw.get("current")) or current_price
        target.low = safe_float(raw.get("low"))
        target.high = safe_float(raw.get("high"))
        target.mean = safe_float(raw.get("mean"))
        target.median = safe_float(raw.get("median"))
        count = raw.get("numberOfAnalysts")
        target.analyst_count = int(count) if count else None
    except Exception as exc:
        errors.append(f"hedef fiyat: {type(exc).__name__}: {exc}")
        log.debug("%s hedef fiyat alınamadı: %s", sym, exc)

    # 2) Tavsiye (AL/TUT/SAT) ve yükseliş potansiyeli
    try:
        rec = ticker.recommendations or {}
        target.recommendation = rec.get("recommendation") or None
        target.upside_percent = safe_float(rec.get("upside_potential"))
        if target.mean is None:
            target.mean = safe_float(rec.get("target_price"))
    except Exception as exc:
        errors.append(f"tavsiye: {type(exc).__name__}: {exc}")
        log.debug("%s tavsiye verisi alınamadı: %s", sym, exc)

    # 3) Al/Tut/Sat dağılımı (tamamen süs; düşerse sessizce geç)
    try:
        summary = ticker.recommendations_summary or {}
        target.summary_counts = {
            RECOMMENDATION_LABELS.get(k, k): int(v)
            for k, v in summary.items()
            if isinstance(v, (int, float)) and v
        }
    except Exception as exc:
        log.debug("%s tavsiye dağılımı alınamadı: %s", sym, exc)

    # Yükseliş potansiyeli gelmediyse ortalama hedeften hesapla
    if target.upside_percent is None and target.mean and target.current:
        target.upside_percent = (target.mean - target.current) / target.current * 100.0

    if target.has_data:
        return FetchResult.success(target, source="borsapy")

    return FetchResult.failure(" | ".join(errors) or "analist verisi bulunamadı")
