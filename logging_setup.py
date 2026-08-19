"""Merkezi loglama kurulumu — hem konsola hem dosyaya yazar."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config import config

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """
    Kök logger'ı yapılandırır. Birden fazla çağrılsa bile bir kez uygulanır
    (Flask reloader gibi durumlarda mükerrer handler eklenmesini önler).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or config.log_level), logging.INFO))

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 2 MB'lık 5 dosyalık döngüsel log
    file_handler = RotatingFileHandler(
        config.log_path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Gürültülü üçüncü parti logger'ları kıs
    for noisy in ("urllib3", "yfinance", "peewee", "websocket", "werkzeug", "borsapy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Modül bazlı logger döndürür."""
    setup_logging()
    return logging.getLogger(name)
