"""
Türkçe sayı biçimlendirme yardımcıları.

Hem e-posta şablonunda (Jinja2 filtresi olarak) hem de alarm metinlerinde
aynı biçim kullanılsın diye ayrı bir modülde tutuluyor — böylece
`report.py` ile `mailer.py` arasında dairesel import oluşmuyor.
"""

from __future__ import annotations


def fmt_number(value: float | None, decimals: int = 2) -> str:
    """1234567.5 -> '1.234.567,50' (binlik nokta, ondalık virgül)."""
    if value is None:
        return "—"
    try:
        formatted = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"
    # Ayraçları takas et: ',' ve '.' yer değiştirir.
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def fmt_tl(value: float | None) -> str:
    """Fiyat gösterimi: '268,50 TL'"""
    if value is None:
        return "—"
    return f"{fmt_number(value)} TL"


def fmt_pct(value: float | None) -> str:
    """İşaretli yüzde: '%+2,45' / '%-1,30'"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"%{fmt_number(v)}" if v < 0 else f"%+{fmt_number(v)}"


def fmt_pct_plain(value: float | None) -> str:
    """İşaretsiz yüzde: '%5,00' — eşik gibi mutlak değerler için."""
    if value is None:
        return "—"
    try:
        return f"%{fmt_number(abs(float(value)))}"
    except (TypeError, ValueError):
        return "—"


def fmt_signed(value: float | None) -> str:
    """İşaretli mutlak değişim: '+6,25'"""
    if value is None:
        return "—"
    try:
        prefix = "+" if float(value) >= 0 else ""
        return f"{prefix}{fmt_number(value)}"
    except (TypeError, ValueError):
        return "—"


def fmt_compact(value: float | None) -> str:
    """Büyük sayıları kısaltır: 1_250_000 -> '1,25 mn'"""
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    for limit, suffix in ((1e9, " mr"), (1e6, " mn"), (1e3, " bin")):
        if abs(v) >= limit:
            return fmt_number(v / limit) + suffix
    return fmt_number(v, 0)
