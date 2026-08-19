"""
SQLite veri katmanı.

Kurulum gerektirmeyen, dosya tabanlı basit çözüm. ORM kullanılmıyor —
şema küçük olduğu için standart kütüphanenin `sqlite3` modülü yeterli.

Tablolar:
  - watchlist : takip edilen hisseler ve alarm ayarları
  - run_log   : her rapor çalıştırmasının sonucu (başarı/hata takibi için)
  - sent_news : daha önce e-postada gönderilmiş haber/bildirimler (tekrarı önler)
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import _harden_permissions, config
from logging_setup import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL UNIQUE,   -- BIST kodu, örn. THYAO
    name         TEXT,                       -- şirket adı (opsiyonel, bilgi amaçlı)
    target_price REAL,                       -- opsiyonel hedef fiyat
    alert_pct    REAL,                       -- opsiyonel alarm eşiği (%)
    notes        TEXT,
    active       INTEGER NOT NULL DEFAULT 1, -- 0 = geçici olarak pasif
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    trigger      TEXT,        -- 'manual' | 'schedule' | 'web'
    symbols      INTEGER,     -- işlenen hisse sayısı
    failures     INTEGER,     -- veri alınamayan hisse sayısı
    email_sent   INTEGER,     -- 0/1
    error        TEXT
);

CREATE TABLE IF NOT EXISTS sent_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    item_key   TEXT NOT NULL,   -- URL veya başlık hash'i
    sent_at    TEXT NOT NULL,
    UNIQUE(symbol, item_key)
);

CREATE INDEX IF NOT EXISTS idx_sent_news_symbol ON sent_news(symbol);
"""


def _now() -> str:
    """UTC ISO-8601 zaman damgası."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """
    Bağlantı context manager'ı. Başarılı çıkışta commit, hatada rollback yapar.
    `row_factory` sayesinde satırlar sözlük gibi kullanılabilir.
    """
    path = Path(db_path or config.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """
    Şemayı oluşturur (idempotent) ve dosya izinlerini sıkılaştırır.

    Veritabanı takip listesini — yani kullanıcının hangi hisselerle
    ilgilendiğini — barındırır. Çok kullanıcılı bir makinede bu bilgi
    başkalarına açık olmamalı (O-6).
    """
    path = Path(db_path or config.db_path)
    with get_conn(path) as conn:
        conn.executescript(SCHEMA)
    _harden_permissions(path)
    log.debug("Veritabanı hazır: %s", path)


# --------------------------------------------------------------------------
# Watchlist CRUD
# --------------------------------------------------------------------------

# BIST hisse kodları 3-6 harf/rakamdır (THYAO, GARAN, ASELS, ISCTR, KRDMD…).
# Bu bir izin listesi (whitelist) kontrolüdür: kalıba uymayan hiçbir girdi
# veritabanına ya da dış isteklere ulaşmaz.
SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,6}$")
MAX_SYMBOL_INPUT = 32


def normalize_symbol(symbol: str) -> str:
    """
    Kullanıcı girdisini standart BIST koduna çevirir.
    'thyao.is' -> 'THYAO', ' garan ' -> 'GARAN'

    Alfanümerik olmayan tüm karakterler atılır — bu, kodun daha sonra
    kullanıldığı hiçbir bağlamda (SQL parametresi, URL parçası, log satırı)
    özel anlam taşıyan karakter kalmamasını garanti eder.
    """
    s = (symbol or "").strip().upper()[:MAX_SYMBOL_INPUT]
    for suffix in (".IS", ".E", ".IST"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    # Türkçe karakter girilmişse ASCII karşılığına çevir (İ -> I gibi)
    trans = str.maketrans("İIŞĞÜÖÇ", "IISGUOC")
    s = s.translate(trans)
    return "".join(ch for ch in s if ch.isalnum())


def validate_symbol_format(symbol: str) -> tuple[bool, str]:
    """
    Normalize edilmiş bir kodun BIST hisse kodu biçimine uyup uymadığını
    kontrol eder.

    Returns:
        (geçerli_mi, geçersizse_gerekçe)
    """
    if not symbol:
        return False, "geçersiz kod"
    if not SYMBOL_RE.match(symbol):
        return False, "BIST kodu 3-6 harf/rakam olmalı"
    return True, ""


def list_stocks(only_active: bool = False, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Takip listesini döndürür."""
    sql = "SELECT * FROM watchlist"
    if only_active:
        sql += " WHERE active = 1"
    sql += " ORDER BY symbol ASC"
    with get_conn(db_path) as conn:
        return [dict(row) for row in conn.execute(sql)]


def get_stock(symbol: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Tek bir hisseyi koduna göre getirir."""
    sym = normalize_symbol(symbol)
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (sym,)).fetchone()
        return dict(row) if row else None


def add_stock(
    symbol: str,
    name: str | None = None,
    target_price: float | None = None,
    alert_pct: float | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """
    Takip listesine hisse ekler.

    Returns:
        (başarılı_mı, mesaj)
    """
    sym = normalize_symbol(symbol)

    # D-2: biçim doğrulaması artık veri katmanında da zorunlu — web arayüzü,
    # CLI ve doğrudan API kullanımı aynı kuralı paylaşır.
    ok, reason = validate_symbol_format(sym)
    if not ok:
        return False, f"Geçersiz hisse kodu: {reason}"

    now = _now()
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                """INSERT INTO watchlist
                   (symbol, name, target_price, alert_pct, notes, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (sym, name, target_price, alert_pct, notes, now, now),
            )
        log.info("Hisse eklendi: %s", sym)
        return True, f"{sym} takip listesine eklendi."
    except sqlite3.IntegrityError:
        return False, f"{sym} zaten takip listesinde."


def update_stock(
    symbol: str,
    target_price: float | None = None,
    alert_pct: float | None = None,
    notes: str | None = None,
    name: str | None = None,
    active: bool | None = None,
    db_path: Path | None = None,
) -> tuple[bool, str]:
    """
    Hissenin alarm/hedef ayarlarını günceller.
    None geçilen alanlar açıkça NULL'a çekilir (temizleme için),
    bu yüzden formdan gelen boş değerler "temizle" anlamına gelir.
    """
    sym = normalize_symbol(symbol)
    existing = get_stock(sym, db_path)
    if not existing:
        return False, f"{sym} takip listesinde bulunamadı."

    with get_conn(db_path) as conn:
        conn.execute(
            """UPDATE watchlist
               SET target_price = ?, alert_pct = ?, notes = ?, name = ?,
                   active = ?, updated_at = ?
               WHERE symbol = ?""",
            (
                target_price,
                alert_pct,
                notes,
                name if name is not None else existing["name"],
                int(existing["active"] if active is None else active),
                _now(),
                sym,
            ),
        )
    log.info("Hisse güncellendi: %s", sym)
    return True, f"{sym} güncellendi."


def set_active(symbol: str, active: bool, db_path: Path | None = None) -> tuple[bool, str]:
    """Hisseyi silmeden geçici olarak pasife alır / geri açar."""
    sym = normalize_symbol(symbol)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE watchlist SET active = ?, updated_at = ? WHERE symbol = ?",
            (int(active), _now(), sym),
        )
    if cur.rowcount == 0:
        return False, f"{sym} bulunamadı."
    return True, f"{sym} {'aktif' if active else 'pasif'} yapıldı."


def remove_stock(symbol: str, db_path: Path | None = None) -> tuple[bool, str]:
    """Hisseyi takip listesinden tamamen siler."""
    sym = normalize_symbol(symbol)
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (sym,))
        conn.execute("DELETE FROM sent_news WHERE symbol = ?", (sym,))
    if cur.rowcount == 0:
        return False, f"{sym} bulunamadı."
    log.info("Hisse silindi: %s", sym)
    return True, f"{sym} takip listesinden çıkarıldı."


# --------------------------------------------------------------------------
# Haber tekrarını önleme
# --------------------------------------------------------------------------

def filter_unseen(
    symbol: str, item_keys: list[str], db_path: Path | None = None
) -> set[str]:
    """
    Verilen anahtarlardan daha önce gönderilmemiş olanları döndürür.
    Aynı KAP bildiriminin her gün tekrar e-postaya girmesini engeller.
    """
    if not item_keys:
        return set()
    sym = normalize_symbol(symbol)
    placeholders = ",".join("?" * len(item_keys))
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT item_key FROM sent_news WHERE symbol = ? AND item_key IN ({placeholders})",
            (sym, *item_keys),
        ).fetchall()
    seen = {r["item_key"] for r in rows}
    return set(item_keys) - seen


def mark_sent(symbol: str, item_keys: list[str], db_path: Path | None = None) -> None:
    """Gönderilen haber anahtarlarını kaydeder."""
    if not item_keys:
        return
    sym = normalize_symbol(symbol)
    now = _now()
    with get_conn(db_path) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO sent_news (symbol, item_key, sent_at) VALUES (?, ?, ?)",
            [(sym, k, now) for k in item_keys],
        )


def prune_sent_news(days: int = 45, db_path: Path | None = None) -> int:
    """Eski haber kayıtlarını temizler; DB'nin şişmesini önler."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(timespec="seconds")
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM sent_news WHERE sent_at < ?", (cutoff_iso,))
    return cur.rowcount


# --------------------------------------------------------------------------
# Çalıştırma logu
# --------------------------------------------------------------------------

def start_run(trigger: str, db_path: Path | None = None) -> int:
    """Yeni bir çalıştırma kaydı açar, id döndürür."""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO run_log (started_at, trigger) VALUES (?, ?)", (_now(), trigger)
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    symbols: int = 0,
    failures: int = 0,
    email_sent: bool = False,
    error: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Çalıştırma kaydını sonuçla kapatır."""
    with get_conn(db_path) as conn:
        conn.execute(
            """UPDATE run_log
               SET finished_at = ?, symbols = ?, failures = ?, email_sent = ?, error = ?
               WHERE id = ?""",
            (_now(), symbols, failures, int(email_sent), error, run_id),
        )


def recent_runs(limit: int = 10, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Son çalıştırmaları döndürür (arayüzdeki durum paneli için)."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
