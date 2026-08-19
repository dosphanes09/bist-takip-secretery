"""
Veri sağlayıcılar için ortak tipler ve yardımcılar.

Tasarım ilkesi: hiçbir sağlayıcı exception fırlatarak tüm raporu düşürmez.
Her fonksiyon `FetchResult` döner; içinde ya veri ya da hata mesajı vardır.
"""

from __future__ import annotations

import functools
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, TypeVar

from logging_setup import get_logger

log = get_logger(__name__)

T = TypeVar("T")


@dataclass
class FetchResult(Generic[T]):
    """
    Bir veri çekme işleminin sonucu.

    Attributes:
        ok:       İşlem başarılı mı.
        data:     Başarılıysa veri.
        error:    Başarısızsa insan okunabilir hata mesajı.
        source:   Veriyi sağlayan kaynağın adı (borsapy, yfinance, kap...).
        warnings: Bloklamayan uyarılar (örn. "analist verisi yok").
    """

    ok: bool
    data: T | None = None
    error: str | None = None
    source: str | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def success(cls, data: T, source: str | None = None) -> "FetchResult[T]":
        return cls(ok=True, data=data, source=source)

    @classmethod
    def failure(cls, error: str, source: str | None = None) -> "FetchResult[T]":
        return cls(ok=False, error=error, source=source)


@dataclass
class PriceData:
    """Bir hissenin güncel fiyat görüntüsü."""

    symbol: str
    last: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: float | None = None          # lot
    amount: float | None = None          # TL hacim
    currency: str = "TRY"
    updated_at: datetime | None = None
    source: str = ""

    def fill_derived(self) -> "PriceData":
        """
        Eksik türev alanları iki yönlü olarak tamamlar.

        Sağlayıcılar farklı alt kümeler döndürür: kimi `change`/`change_percent`
        verip önceki kapanışı atlar, kimi tam tersi. Elde olandan türetilebilen
        her alanı doldururuz — böylece rapordaki kutular boş kalmaz.
        """
        # 1) Önceki kapanış yoksa, son fiyat ve değişimden geri hesapla.
        if self.previous_close is None and self.last is not None:
            if self.change is not None:
                self.previous_close = self.last - self.change
            elif self.change_percent not in (None, -100):
                self.previous_close = self.last / (1 + self.change_percent / 100.0)

        # 2) Değişim yoksa, önceki kapanıştan hesapla.
        if self.change is None and self.last is not None and self.previous_close is not None:
            self.change = self.last - self.previous_close
        if (
            self.change_percent is None
            and self.last is not None
            and self.previous_close not in (None, 0)
        ):
            self.change_percent = (self.last - self.previous_close) / self.previous_close * 100.0

        return self


@dataclass
class NewsItem:
    """Bir haber ya da KAP bildirimi."""

    title: str
    url: str
    source: str                      # "KAP" | "Google News" | yayın adı
    published: datetime | None = None
    summary: str = ""
    kind: str = "news"               # "kap" | "news"

    @property
    def key(self) -> str:
        """Tekrar kontrolü için benzersiz anahtar."""
        return self.url or f"{self.source}:{self.title}"


@dataclass
class AnalystTarget:
    """Analist hedef fiyat özeti."""

    symbol: str
    current: float | None = None
    low: float | None = None
    high: float | None = None
    mean: float | None = None
    median: float | None = None
    analyst_count: int | None = None
    recommendation: str | None = None       # AL / TUT / SAT
    upside_percent: float | None = None
    summary_counts: dict[str, int] = field(default_factory=dict)
    source: str = ""

    @property
    def has_data(self) -> bool:
        """Gösterilmeye değer bir veri var mı?"""
        return any(
            v is not None for v in (self.mean, self.median, self.high, self.low, self.recommendation)
        )


@dataclass
class StockReport:
    """Tek bir hisse için toplanmış tüm rapor verisi."""

    symbol: str
    name: str | None = None
    target_price: float | None = None
    alert_pct: float | None = None

    price: PriceData | None = None
    price_error: str | None = None

    kap_items: list[NewsItem] = field(default_factory=list)
    kap_error: str | None = None

    news_items: list[NewsItem] = field(default_factory=list)
    news_error: str | None = None

    analyst: AnalystTarget | None = None
    analyst_error: str | None = None

    alerts: list[str] = field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.price is not None and self.price.last is not None

    @property
    def all_items(self) -> list[NewsItem]:
        """KAP bildirimleri önce, sonra genel haberler."""
        return self.kap_items + self.news_items


def retry(times: int = 2, delay: float = 1.5, exceptions: tuple = (Exception,)) -> Callable:
    """
    Basit yeniden deneme dekoratörü.

    Ağ kaynaklı geçici hatalar için; son deneme de başarısızsa exception
    yukarı fırlatılır ve çağıran taraf FetchResult.failure üretir.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < times:
                        log.debug(
                            "%s başarısız (deneme %d/%d): %s — %.1fs sonra tekrar",
                            func.__name__, attempt, times, exc, delay,
                        )
                        time.sleep(delay)
            # `assert` yerine açık raise: Python -O ile çalıştırıldığında
            # assert ifadeleri kaldırılır ve kontrol akışı sessizce bozulur.
            if last_exc is None:
                raise RuntimeError(f"{func.__name__}: beklenmeyen yeniden deneme durumu")
            raise last_exc

        return wrapper

    return decorator


class RateLimiter:
    """
    Basit thread-safe hız sınırlayıcı (D-1).

    Dış kaynaklara saniyede belirli sayıdan fazla istek gitmesini engeller.
    Hisseler paralel işlendiği için, sınırlayıcı olmadan 50 hisselik bir
    liste birkaç saniye içinde yüzlerce istek üretir ve KAP/Google News
    tarafında IP engeline yol açabilir.
    """

    def __init__(self, min_interval: float = 0.25) -> None:
        self._min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        """Gerekiyorsa bir sonraki isteğe kadar bekler."""
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


# Kaynak başına ayrı sınırlayıcılar — biri diğerini yavaşlatmasın.
kap_limiter = RateLimiter(0.35)
news_limiter = RateLimiter(0.50)
price_limiter = RateLimiter(0.20)


def safe_float(value: Any) -> float | None:
    """Herhangi bir değeri güvenli şekilde float'a çevirir; olmazsa None."""
    if value is None:
        return None
    try:
        # pandas NaN kontrolü (NaN != NaN)
        f = float(value)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None
