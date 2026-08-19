"""
Fiyat verisi sağlayıcıları.

Öncelik zinciri (config.PRICE_PROVIDERS ile değiştirilebilir):
  1. borsapy  — BIST'e özel; TradingView + İş Yatırım kaynaklı zengin veri.
  2. yfinance — Yahoo Finance; BIST kodları `.IS` son ekiyle (THYAO.IS).

Bir sağlayıcı hata verirse zincirdeki sıradaki denenir. Hepsi başarısızsa
FetchResult.failure döner ve rapor o hisse için "veri alınamadı" gösterir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from logging_setup import get_logger
from providers.base import FetchResult, PriceData, price_limiter, safe_float

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# borsapy
# ---------------------------------------------------------------------------

def fetch_borsapy(symbol: str) -> PriceData:
    """
    borsapy üzerinden fiyat çeker.

    `Ticker.info` alanları: last, open, high, low, close (önceki kapanış),
    volume (lot), amount (TL), change, change_percent, update_time.
    """
    import borsapy as bp  # yerel import: kütüphane yoksa diğer sağlayıcı çalışsın

    ticker = bp.Ticker(symbol)
    info = ticker.info

    # Alan adı uyumluluğu: borsapy'nin TradingView sağlayıcısı önceki kapanışı
    # `prev_close`, zaman damgasını `timestamp` adıyla döndürür. Kütüphanenin
    # kendi takma ad tablosu ise `close` / `update_time` adlarını kullanır.
    # İkisini de deneyerek sürüm farklarına karşı dayanıklı oluyoruz.
    updated = None
    for time_key in ("update_time", "timestamp", "lp_time"):
        raw_time = info.get(time_key)
        if raw_time:
            updated = _parse_dt(raw_time)
            if updated:
                break

    previous_close = None
    for close_key in ("prev_close", "close", "previous_close"):
        previous_close = safe_float(info.get(close_key))
        if previous_close is not None:
            break

    price = PriceData(
        symbol=symbol,
        last=safe_float(info.get("last")),
        open=safe_float(info.get("open")),
        high=safe_float(info.get("high")),
        low=safe_float(info.get("low")),
        previous_close=previous_close,
        change=safe_float(info.get("change")),
        change_percent=safe_float(info.get("change_percent")),
        volume=safe_float(info.get("volume")),
        amount=safe_float(info.get("amount")),
        updated_at=updated,
        source="borsapy",
    )

    if price.last is None:
        # Canlı kotasyon boşsa son iki günlük mumdan türet.
        price = _borsapy_from_history(ticker, symbol)

    return price.fill_derived()


def _borsapy_from_history(ticker, symbol: str) -> PriceData:
    """Canlı kotasyon gelmezse günlük OHLCV geçmişinden son mumu kullanır."""
    hist = ticker.history(period="5d", interval="1d")
    if hist is None or hist.empty:
        raise ValueError("borsapy geçmiş veri döndürmedi")

    last_row = hist.iloc[-1]
    prev_close = safe_float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None

    return PriceData(
        symbol=symbol,
        last=safe_float(last_row.get("Close")),
        open=safe_float(last_row.get("Open")),
        high=safe_float(last_row.get("High")),
        low=safe_float(last_row.get("Low")),
        previous_close=prev_close,
        volume=safe_float(last_row.get("Volume")),
        updated_at=_index_to_dt(hist.index[-1]),
        source="borsapy (geçmiş)",
    )


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------

def fetch_yfinance(symbol: str) -> PriceData:
    """
    yfinance üzerinden fiyat çeker. BIST kodları `.IS` son ekiyle sorgulanır.

    Önce hafif `fast_info` denenir; yetersizse günlük geçmişe düşülür.
    """
    import yfinance as yf

    yf_symbol = f"{symbol}.IS"
    ticker = yf.Ticker(yf_symbol)

    last = open_ = high = low = prev_close = volume = None

    try:
        fi = ticker.fast_info
        last = safe_float(_fi_get(fi, "lastPrice", "last_price"))
        open_ = safe_float(_fi_get(fi, "open"))
        high = safe_float(_fi_get(fi, "dayHigh", "day_high"))
        low = safe_float(_fi_get(fi, "dayLow", "day_low"))
        prev_close = safe_float(_fi_get(fi, "previousClose", "previous_close"))
        volume = safe_float(_fi_get(fi, "lastVolume", "last_volume"))
    except Exception as exc:  # fast_info bazı sürümlerde kırılgan
        log.debug("yfinance fast_info başarısız (%s): %s", yf_symbol, exc)

    updated = None
    if last is None or prev_close is None:
        hist = ticker.history(period="5d", interval="1d")
        if hist is None or hist.empty:
            raise ValueError("yfinance veri döndürmedi")
        row = hist.iloc[-1]
        last = last if last is not None else safe_float(row.get("Close"))
        open_ = open_ if open_ is not None else safe_float(row.get("Open"))
        high = high if high is not None else safe_float(row.get("High"))
        low = low if low is not None else safe_float(row.get("Low"))
        volume = volume if volume is not None else safe_float(row.get("Volume"))
        if prev_close is None and len(hist) >= 2:
            prev_close = safe_float(hist["Close"].iloc[-2])
        updated = _index_to_dt(hist.index[-1])

    if last is None:
        raise ValueError("yfinance son fiyat döndürmedi")

    return PriceData(
        symbol=symbol,
        last=last,
        open=open_,
        high=high,
        low=low,
        previous_close=prev_close,
        volume=volume,
        updated_at=updated,
        source="yfinance",
    ).fill_derived()


def _fi_get(fast_info, *keys: str):
    """yfinance fast_info sürümler arası farklı isimlendirme kullanabiliyor."""
    for key in keys:
        try:
            value = fast_info[key]
            if value is not None:
                return value
        except Exception:
            pass
        value = getattr(fast_info, key, None)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Zincir
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, Callable[[str], PriceData]] = {
    "borsapy": fetch_borsapy,
    "yfinance": fetch_yfinance,
}


def get_price(symbol: str, provider_order: list[str] | None = None) -> FetchResult[PriceData]:
    """
    Fiyatı öncelik sırasına göre dener. İlk başarılı sonucu döndürür.

    Hiçbiri çalışmazsa tüm hata mesajlarını birleştirip failure döner —
    böylece log'a bakınca hangi kaynağın neden düştüğü görülür.
    """
    from config import config

    order = provider_order or config.price_providers
    errors: list[str] = []

    for name in order:
        func = PROVIDERS.get(name)
        if func is None:
            errors.append(f"{name}: bilinmeyen sağlayıcı")
            continue
        try:
            price_limiter.wait()  # D-1: kaynağa nazik davran
            price = func(symbol)
            if price.last is None:
                raise ValueError("son fiyat boş")
            log.info("%s fiyatı %s ile alındı: %.2f", symbol, price.source, price.last)
            return FetchResult.success(price, source=price.source)
        except Exception as exc:
            msg = f"{name}: {type(exc).__name__}: {exc}"
            errors.append(msg)
            log.warning("%s için fiyat alınamadı — %s", symbol, msg)

    return FetchResult.failure(" | ".join(errors) or "sağlayıcı tanımlı değil")


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def _parse_dt(value) -> datetime | None:
    """Sağlayıcıdan gelen çeşitli tarih formatlarını datetime'a çevirir."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value.strip()[:19], fmt)
            except ValueError:
                continue
    return None


def _index_to_dt(index_value) -> datetime | None:
    """pandas Timestamp -> datetime (timezone bilgisi düşürülür)."""
    try:
        dt = index_value.to_pydatetime()
        return dt.replace(tzinfo=None)
    except Exception:
        return None
