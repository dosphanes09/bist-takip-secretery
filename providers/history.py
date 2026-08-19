"""
Geçmiş fiyat verisi ve dönem performansı.

Hisse detay sayfası için OHLCV serisi ve 1G/1H/1A/3A/6A/1Y dönemlerinin
yüzde değişimlerini üretir.

Fiyat sağlayıcılarıyla aynı zincir mantığı: önce borsapy, olmazsa yfinance.
Hiçbiri çalışmazsa FetchResult.failure döner ve sayfa "veri alınamadı" gösterir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from logging_setup import get_logger
from providers.base import FetchResult, price_limiter, retry, safe_float

log = get_logger(__name__)


# Dönem tanımları: arayüzdeki etiket → (sağlayıcı dönemi, mum aralığı, açıklama)
# Kısa dönemlerde gün içi mum kullanılır; uzun dönemlerde günlük.
PERIODS: dict[str, dict[str, str]] = {
    "1g": {"period": "1d",  "interval": "15m", "label": "1 Gün",   "short": "1G"},
    "1h": {"period": "5d",  "interval": "1h",  "label": "1 Hafta", "short": "1H"},
    "1a": {"period": "1mo", "interval": "1d",  "label": "1 Ay",    "short": "1A"},
    "3a": {"period": "3mo", "interval": "1d",  "label": "3 Ay",    "short": "3A"},
    "6a": {"period": "6mo", "interval": "1d",  "label": "6 Ay",    "short": "6A"},
    "1y": {"period": "1y",  "interval": "1d",  "label": "1 Yıl",   "short": "1Y"},
}

DEFAULT_PERIOD = "1a"

# Grafikte çizilecek en fazla nokta. Daha fazlası hem yavaş hem okunmaz;
# fazlası varsa eşit aralıklarla seyreltilir.
MAX_POINTS = 400


@dataclass
class Candle:
    """Tek bir zaman dilimi (mum)."""

    date: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": self.date.isoformat(),
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
        }


@dataclass
class History:
    """Bir dönemin fiyat serisi ve özet istatistikleri."""

    symbol: str
    period: str
    interval: str
    candles: list[Candle] = field(default_factory=list)
    source: str = ""

    # --- Özet istatistikler ---

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.candles if c.close is not None]

    @property
    def first_close(self) -> float | None:
        closes = self.closes
        return closes[0] if closes else None

    @property
    def last_close(self) -> float | None:
        closes = self.closes
        return closes[-1] if closes else None

    @property
    def change(self) -> float | None:
        if self.first_close is None or self.last_close is None:
            return None
        return self.last_close - self.first_close

    @property
    def change_percent(self) -> float | None:
        if not self.first_close or self.last_close is None:
            return None
        return (self.last_close - self.first_close) / self.first_close * 100.0

    @property
    def high(self) -> float | None:
        highs = [c.high for c in self.candles if c.high is not None]
        return max(highs) if highs else (max(self.closes) if self.closes else None)

    @property
    def low(self) -> float | None:
        lows = [c.low for c in self.candles if c.low is not None]
        return min(lows) if lows else (min(self.closes) if self.closes else None)

    @property
    def average_volume(self) -> float | None:
        volumes = [c.volume for c in self.candles if c.volume]
        return sum(volumes) / len(volumes) if volumes else None

    def as_dict(self) -> dict[str, Any]:
        """Tarayıcıya gönderilecek JSON gövdesi."""
        return {
            "symbol": self.symbol,
            "period": self.period,
            "interval": self.interval,
            "source": self.source,
            "candles": [c.as_dict() for c in self.candles],
            "summary": {
                "first": self.first_close,
                "last": self.last_close,
                "change": self.change,
                "change_percent": self.change_percent,
                "high": self.high,
                "low": self.low,
                "average_volume": self.average_volume,
                "count": len(self.candles),
            },
        }


# ---------------------------------------------------------------------------
# DataFrame → Candle dönüşümü
# ---------------------------------------------------------------------------

def _index_to_datetime(value: Any) -> datetime | None:
    """pandas indeks değerini datetime'a çevirir (saat dilimi düşürülür)."""
    try:
        dt = value.to_pydatetime()
        return dt.replace(tzinfo=None)
    except AttributeError:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
    except Exception:
        pass
    return None


def _dataframe_to_candles(df: Any) -> list[Candle]:
    """
    OHLCV DataFrame'ini Candle listesine çevirir.

    Sütun adları sağlayıcılar arasında büyük/küçük harf olarak değişebildiği
    için eşleme büyük/küçük harf duyarsız yapılır.
    """
    if df is None or getattr(df, "empty", True):
        return []

    columns = {str(c).lower(): c for c in df.columns}

    def col(name: str):
        return columns.get(name)

    candles: list[Candle] = []
    for index_value, row in df.iterrows():
        date = _index_to_datetime(index_value)
        if date is None:
            continue
        close = safe_float(row.get(col("close"))) if col("close") else None
        if close is None:
            continue  # kapanışı olmayan satır çizilemez
        candles.append(
            Candle(
                date=date,
                open=safe_float(row.get(col("open"))) if col("open") else None,
                high=safe_float(row.get(col("high"))) if col("high") else None,
                low=safe_float(row.get(col("low"))) if col("low") else None,
                close=close,
                volume=safe_float(row.get(col("volume"))) if col("volume") else None,
            )
        )
    return candles


def _downsample(candles: list[Candle], limit: int = MAX_POINTS) -> list[Candle]:
    """
    Nokta sayısını sınırlar. İlk ve son mum her zaman korunur — dönem başı ve
    sonu yüzde hesabında kullanıldığı için seyreltme sonucu değiştirmemeli.
    """
    if len(candles) <= limit:
        return candles
    step = len(candles) / limit
    picked = [candles[int(i * step)] for i in range(limit)]
    if picked[0] is not candles[0]:
        picked[0] = candles[0]
    picked[-1] = candles[-1]
    return picked


# ---------------------------------------------------------------------------
# Sağlayıcılar
# ---------------------------------------------------------------------------

@retry(times=2, delay=1.0)
def _fetch_borsapy(symbol: str, period: str, interval: str):
    import borsapy as bp

    return bp.Ticker(symbol).history(period=period, interval=interval)


@retry(times=2, delay=1.0)
def _fetch_yfinance(symbol: str, period: str, interval: str):
    import yfinance as yf

    return yf.Ticker(f"{symbol}.IS").history(period=period, interval=interval)


def get_history(symbol: str, period_key: str = DEFAULT_PERIOD) -> FetchResult[History]:
    """
    Bir hisse için belirtilen dönemin fiyat serisini döndürür.

    Args:
        symbol:     BIST kodu (THYAO)
        period_key: PERIODS sözlüğündeki anahtar (1g, 1h, 1a, 3a, 6a, 1y)
    """
    spec = PERIODS.get(period_key)
    if spec is None:
        return FetchResult.failure(f"bilinmeyen dönem: {period_key}")

    sym = symbol.upper()
    errors: list[str] = []

    for name, fetcher in (("borsapy", _fetch_borsapy), ("yfinance", _fetch_yfinance)):
        try:
            price_limiter.wait()
            df = fetcher(sym, spec["period"], spec["interval"])
            candles = _downsample(_dataframe_to_candles(df))
            if not candles:
                raise ValueError("boş seri döndü")

            history = History(
                symbol=sym,
                period=period_key,
                interval=spec["interval"],
                candles=candles,
                source=name,
            )
            log.info(
                "%s %s geçmişi %s ile alındı: %d nokta",
                sym, period_key, name, len(candles),
            )
            return FetchResult.success(history, source=name)

        except Exception as exc:
            message = f"{name}: {type(exc).__name__}: {exc}"
            errors.append(message)
            log.warning("%s %s geçmişi alınamadı — %s", sym, period_key, message)

    return FetchResult.failure(" | ".join(errors))


def get_performance(symbol: str) -> dict[str, dict[str, Any]]:
    """
    Tüm dönemler için yüzde değişimi hesaplar.

    Uzun dönemi (1Y) bir kez çekip kısa dönemleri onun içinden türetmek
    cazip görünüyor ama doğru değil: 1G gün içi mum gerektiriyor ve borsa
    tatilleri gün sayısını kaydırıyor. Her dönem kendi isteğiyle alınıyor;
    biri düşerse diğerleri etkilenmiyor.

    Returns:
        {"1a": {"label": "1 Ay", "change_percent": 5.4, "ok": True}, ...}
    """
    results: dict[str, dict[str, Any]] = {}

    for key, spec in PERIODS.items():
        result = get_history(symbol, key)
        if result.ok and result.data:
            results[key] = {
                "label": spec["label"],
                "short": spec["short"],
                "change_percent": result.data.change_percent,
                "first": result.data.first_close,
                "last": result.data.last_close,
                "high": result.data.high,
                "low": result.data.low,
                "ok": True,
            }
        else:
            results[key] = {
                "label": spec["label"],
                "short": spec["short"],
                "change_percent": None,
                "ok": False,
                "error": result.error,
            }

    return results
