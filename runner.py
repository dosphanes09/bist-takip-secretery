"""
Çalıştırma orkestrasyonu: rapor üret → e-posta gönder → sonucu logla.

Hem CLI (`--run-now`), hem web arayüzündeki "Şimdi çalıştır" düğmesi,
hem de zamanlayıcı bu tek giriş noktasını kullanır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import db
from config import config
from logging_setup import get_logger
from mailer import send_report
from report import Report, build_report, mark_report_sent

log = get_logger(__name__)


@dataclass
class RunResult:
    """Bir çalıştırmanın özeti."""

    ok: bool
    message: str
    report: Report | None = None
    email_sent: bool = False

    @property
    def summary(self) -> str:
        if self.report is None:
            return self.message
        r = self.report
        return (
            f"{len(r.stocks)} hisse · {r.failures} başarısız · "
            f"{r.total_news} haber · {r.alert_count} alarm — {self.message}"
        )


def is_trading_day(when: datetime | None = None) -> bool:
    """Hafta içi mi? (Resmî tatil kontrolü yapılmaz — piyasa kapalıysa
    veri değişmez, rapor yine de gönderilebilir.)"""
    day = (when or datetime.now()).weekday()
    return day < 5  # 0=Pazartesi … 4=Cuma


def run_once(
    trigger: str = "manual",
    dry_run: bool = False,
    symbols: list[str] | None = None,
    force: bool = False,
    send_email: bool = True,
) -> RunResult:
    """
    Tam bir rapor döngüsü çalıştırır.

    Args:
        trigger:    Log için kaynak etiketi ('manual' | 'schedule' | 'web').
        dry_run:    E-posta göndermez; HTML'i `outbox/` klasörüne yazar.
        symbols:    Sadece bu hisseler (None ise takip listesinin tamamı).
        force:      Hafta sonu atlama kuralını yok say.
        send_email: False ise rapor üretilir ama gönderilmez (önizleme için).

    Bu fonksiyon exception fırlatmaz; her sonuç RunResult olarak döner.
    """
    if not force and config.skip_weekends and not is_trading_day():
        msg = "Hafta sonu — rapor atlandı (SKIP_WEEKENDS=false ile kapatabilirsin)."
        log.info(msg)
        return RunResult(ok=True, message=msg)

    db.init_db()
    run_id = db.start_run(trigger)
    email_sent = False

    try:
        report = build_report(symbols=symbols, skip_seen_news=True)

        if not report.stocks:
            msg = "Takip listesi boş — gönderilecek bir şey yok."
            db.finish_run(run_id, symbols=0, failures=0, email_sent=False, error=msg)
            return RunResult(ok=False, message=msg, report=report)

        if send_email:
            email_sent, mail_msg = send_report(report, dry_run=dry_run)
        else:
            mail_msg = "E-posta gönderimi kapalı (--no-email)."

        # Haberler YALNIZCA gönderim başarılı olduktan sonra "gönderildi"
        # işaretlenir. Aksi halde gönderim çöktüğünde o günün haberleri
        # kalıcı olarak kaybolur ve bir daha hiçbir e-postada görünmez.
        if email_sent and not dry_run:
            mark_report_sent(report)

        db.finish_run(
            run_id,
            symbols=len(report.stocks),
            failures=report.failures,
            email_sent=email_sent,
            error=None if email_sent or not send_email else mail_msg,
        )

        # Eski haber kayıtlarını ara sıra temizle
        try:
            db.prune_sent_news()
        except Exception as exc:
            log.debug("sent_news temizliği atlandı: %s", exc)

        return RunResult(
            ok=email_sent or not send_email,
            message=mail_msg,
            report=report,
            email_sent=email_sent,
        )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.exception("Çalıştırma başarısız")
        db.finish_run(run_id, error=error)
        return RunResult(ok=False, message=f"Çalıştırma başarısız: {error}")
