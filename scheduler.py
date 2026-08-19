"""
Zamanlayıcı — `schedule` kütüphanesiyle günlük (ve opsiyonel gün içi) çalışma.

İki kullanım biçimi desteklenir:

  1. Sürekli çalışan mod: `python app.py --schedule`
     Süreç açık kaldığı sürece DAILY_SEND_TIME'da raporu gönderir.

  2. Sistem zamanlayıcısı (cron / Windows Task Scheduler):
     `python app.py --run-now` komutunu istediğin saatte tetikler.
     Bilgisayar/sunucu sürekli açık değilse bu daha güvenilirdir.

Not: `schedule` kütüphanesi sistemin yerel saatini kullanır. Makinenin saat
dilimi Europe/Istanbul değilse TIMEZONE ayarına göre saat kaydırması yapılır.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from config import config
from logging_setup import get_logger
from runner import run_once

log = get_logger(__name__)


def _local_offset_shift(target_tz: str) -> timedelta:
    """
    Hedef saat dilimi ile makinenin yerel saati arasındaki farkı hesaplar.

    `schedule` kütüphanesi saat dilimi bilmez; bu yüzden "TSİ 18:30"u
    makinenin yerel saatine çevirip öyle planlıyoruz.
    """
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now().astimezone()
        target_now = datetime.now(ZoneInfo(target_tz))
        local_offset = now.utcoffset() or timedelta(0)
        target_offset = target_now.utcoffset() or timedelta(0)
        return target_offset - local_offset
    except Exception as exc:
        log.warning("Saat dilimi farkı hesaplanamadı (%s): %s", target_tz, exc)
        return timedelta(0)


def to_local_time(hhmm: str, target_tz: str | None = None) -> str:
    """
    'HH:MM' biçimindeki hedef saat dilimi saatini makinenin yerel saatine çevirir.
    Makine zaten aynı saat dilimindeyse değer değişmez.
    """
    tz = target_tz or config.timezone
    try:
        hour, minute = (int(p) for p in hhmm.strip().split(":")[:2])
    except (ValueError, IndexError):
        log.error("Geçersiz saat formatı '%s' — 18:30 varsayılıyor", hhmm)
        hour, minute = 18, 30

    shift = _local_offset_shift(tz)
    if shift == timedelta(0):
        return f"{hour:02d}:{minute:02d}"

    base = datetime(2000, 1, 1, hour, minute) - shift
    return base.strftime("%H:%M")


def _job(label: str) -> None:
    """Zamanlayıcının tetiklediği iş."""
    log.info("=== Zamanlanmış çalıştırma başlıyor (%s) ===", label)
    result = run_once(trigger="schedule")
    log.info("=== Zamanlanmış çalıştırma bitti: %s ===", result.summary)


def build_schedule():
    """
    Zamanlayıcıyı kurar ve planlanan saatleri döndürür.

    Not: `schedule` modülünün kendisi değil, `default_scheduler` nesnesi
    kullanılır. Modül seviyesindeki `schedule.next_run` bir **fonksiyondur**,
    Scheduler nesnesindeki ise bir **property** — nesneyi kullanmak ikisini
    karıştırma riskini ortadan kaldırır.

    Returns:
        (Scheduler nesnesi, planlanan yerel saatlerin listesi)
    """
    import schedule

    scheduler = schedule.default_scheduler
    scheduler.clear()
    planned: list[str] = []

    daily_local = to_local_time(config.daily_send_time)
    scheduler.every().day.at(daily_local).do(_job, label="günlük")
    planned.append(daily_local)
    log.info(
        "Günlük rapor planlandı: %s (%s) → makine saati %s",
        config.daily_send_time, config.timezone, daily_local,
    )

    if config.intraday_enabled:
        for raw_time in config.intraday_times:
            local = to_local_time(raw_time)
            scheduler.every().day.at(local).do(_job, label=f"gün içi {raw_time}")
            planned.append(local)
            log.info("Gün içi kontrol planlandı: %s → makine saati %s", raw_time, local)

    if not planned:
        log.warning("Hiçbir zamanlama kurulamadı.")

    return scheduler, planned


def run_scheduler(run_immediately: bool = False) -> None:
    """
    Zamanlayıcıyı ön planda çalıştırır (Ctrl+C ile durdurulur).

    Args:
        run_immediately: Başlar başlamaz bir kez rapor gönder (test için).
    """
    scheduler, planned = build_schedule()

    if run_immediately:
        log.info("Başlangıçta bir kez çalıştırılıyor…")
        _job("başlangıç")

    next_run = scheduler.next_run
    log.info(
        "Zamanlayıcı çalışıyor. Planlanan saatler: %s. Sıradaki: %s. Durdurmak için Ctrl+C.",
        ", ".join(planned) or "yok",
        next_run.strftime("%d.%m.%Y %H:%M") if next_run else "—",
    )

    try:
        while True:
            scheduler.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Zamanlayıcı durduruldu.")
