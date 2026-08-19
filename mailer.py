"""
E-posta oluşturma ve gönderme.

HTML gövde Jinja2 ile `templates/email.html`'den render edilir; okuyamayan
istemciler için düz metin alternatifi de eklenir (multipart/alternative).

Gönderim standart kütüphanenin `smtplib` modülüyle yapılır. Kimlik bilgileri
yalnızca `.env` üzerinden gelir.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import BASE_DIR, config
from formatting import fmt_compact, fmt_number, fmt_pct, fmt_signed, fmt_tl
from logging_setup import get_logger
from report import Report

log = get_logger(__name__)

TEMPLATE_DIR = BASE_DIR / "templates"


# ---------------------------------------------------------------------------
# Jinja2 filtreleri — sayıları Türkçe biçimde göstermek için
# ---------------------------------------------------------------------------

# Biçimlendirme mantığı `formatting.py`'de; burada Jinja2 filtre adlarına bağlanır.
filter_num = fmt_number
filter_tl = fmt_tl
filter_pct = fmt_pct
filter_signed = fmt_signed
filter_compact = fmt_compact


def build_env(template_dir: Path | None = None) -> Environment:
    """Filtreleri kayıtlı Jinja2 ortamını kurar."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir or TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = filter_num
    env.filters["tl"] = filter_tl
    env.filters["pct"] = filter_pct
    env.filters["signed"] = filter_signed
    env.filters["compact"] = filter_compact
    return env


# ---------------------------------------------------------------------------
# Gövde üretimi
# ---------------------------------------------------------------------------

def build_subject(report: Report) -> str:
    """Konu satırı: tarih + kısa özet + alarm sayısı."""
    date_str = report.generated_at.strftime("%d.%m.%Y")
    parts = [f"BIST Takip · {date_str}"]

    if report.alert_count:
        parts.append(f"{report.alert_count} alarm")
    if report.stocks:
        parts.append(f"{len(report.gainers)}↑ {len(report.losers)}↓")

    return " · ".join(parts)


def render_html(report: Report) -> str:
    """HTML gövdeyi üretir."""
    env = build_env()
    template = env.get_template("email.html")
    return template.render(
        report=report,
        subject=build_subject(report),
        generated_at=report.generated_at.strftime("%d.%m.%Y %H:%M"),
        web_host=config.flask_host,
        web_port=config.flask_port,
    )


def render_text(report: Report) -> str:
    """
    Düz metin alternatifi. HTML desteklemeyen istemciler ve
    spam filtreleri için multipart mesajlarda bulunması iyidir.
    """
    lines = [
        "BIST TAKİP RAPORU",
        report.generated_at.strftime("%d.%m.%Y %H:%M"),
        "=" * 52,
        "",
    ]

    for stock in report.stocks:
        header = stock.symbol + (f" — {stock.name}" if stock.name else "")
        lines.append(header)
        lines.append("-" * len(header))

        if stock.has_price:
            p = stock.price
            lines.append(
                f"  Fiyat: {filter_tl(p.last)}  "
                f"({filter_signed(p.change)} / {filter_pct(p.change_percent)})"
            )
            lines.append(
                f"  Açılış {filter_num(p.open)} · Yük/Düş {filter_num(p.high)}/{filter_num(p.low)} "
                f"· Önceki kapanış {filter_num(p.previous_close)} · Hacim {filter_compact(p.volume)}"
            )
        else:
            lines.append(f"  Fiyat verisi alınamadı. ({stock.price_error or 'bilinmeyen hata'})")

        for alert in stock.alerts:
            lines.append(f"  [ALARM] {alert}")

        if stock.analyst and stock.analyst.has_data:
            a = stock.analyst
            lines.append(
                f"  Analist: ortalama {filter_num(a.mean)} · aralık "
                f"{filter_num(a.low)}–{filter_num(a.high)} · {a.analyst_count or '?'} analist"
                + (f" · {a.recommendation}" if a.recommendation else "")
            )

        for item in stock.all_items:
            tag = "KAP" if item.kind == "kap" else item.source
            when = item.published.strftime("%d.%m %H:%M") if item.published else ""
            lines.append(f"  * [{tag}] {item.title} {when}".rstrip())
            if item.summary:
                lines.append(f"      {item.summary}")
            if item.url:
                lines.append(f"      {item.url}")

        if not stock.all_items:
            lines.append("  (Yeni bildirim veya haber yok.)")

        lines.append("")

    for err in report.global_errors:
        lines.append(f"! {err}")

    lines.append("")
    lines.append("Bu rapor otomatik üretilmiştir; yatırım tavsiyesi değildir.")
    return "\n".join(lines)


def build_message(report: Report) -> EmailMessage:
    """
    Gönderilmeye hazır multipart e-posta nesnesini kurar.

    Kodlama notu: `EmailMessage` varsayılan olarak `policy.default` kullanır ve
    başlıklardaki Türkçe karakterleri RFC 2047'ye göre kendisi kodlar. Başlığa
    eski API'nin `email.header.Header` nesnesini vermek
    `TypeError: 'Header' object is not subscriptable` ile çöker — bu yüzden
    başlıklara **düz string** atanır.
    """
    smtp = config.smtp
    message = EmailMessage()
    message["Subject"] = build_subject(report)
    message["From"] = formataddr((smtp.mail_from_name, smtp.mail_from))
    message["To"] = ", ".join(smtp.mail_to)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="bist-takip.local")

    message.set_content(render_text(report), subtype="plain", charset="utf-8")
    message.add_alternative(render_html(report), subtype="html", charset="utf-8")
    return message


# ---------------------------------------------------------------------------
# Gönderim
# ---------------------------------------------------------------------------

def send_report(report: Report, dry_run: bool = False) -> tuple[bool, str]:
    """
    Raporu e-posta olarak gönderir.

    Args:
        dry_run: True ise SMTP'ye bağlanmaz, HTML'i `outbox/` klasörüne yazar.
                 SMTP kurmadan çıktıyı görmek için kullanışlıdır.

    Returns:
        (başarılı_mı, mesaj)
    """
    if dry_run:
        return _write_preview(report)

    smtp_cfg = config.smtp
    if not smtp_cfg.is_configured:
        missing = ", ".join(smtp_cfg.missing_fields())
        msg = f"SMTP ayarları eksik: {missing}. .env dosyanı kontrol et."
        log.error(msg)
        return False, msg

    message = build_message(report)

    try:
        if smtp_cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout, context=context
            ) as server:
                server.login(smtp_cfg.user, smtp_cfg.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout) as server:
                server.ehlo()
                if smtp_cfg.use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(smtp_cfg.user, smtp_cfg.password)
                server.send_message(message)

        recipients = ", ".join(smtp_cfg.mail_to)
        log.info("E-posta gönderildi → %s", recipients)
        return True, f"E-posta gönderildi: {recipients}"

    except smtplib.SMTPAuthenticationError as exc:
        msg = (
            "SMTP kimlik doğrulama hatası. Gmail kullanıyorsan normal şifre değil "
            f"'Uygulama Şifresi' (App Password) gerekiyor. Detay: {exc}"
        )
        log.error(msg)
        return False, msg
    except Exception as exc:
        msg = f"E-posta gönderilemedi: {type(exc).__name__}: {exc}"
        log.error(msg)
        return False, msg


def _write_preview(report: Report) -> tuple[bool, str]:
    """Dry-run: HTML çıktıyı diske yazar, tarayıcıda açılabilir."""
    outbox = BASE_DIR / "outbox"
    outbox.mkdir(exist_ok=True)
    filename = outbox / f"rapor-{report.generated_at.strftime('%Y%m%d-%H%M%S')}.html"
    filename.write_text(render_html(report), encoding="utf-8")
    log.info("Dry-run: HTML önizleme yazıldı → %s", filename)
    return True, f"Dry-run — e-posta gönderilmedi. Önizleme: {filename}"


def send_test_email() -> tuple[bool, str]:
    """
    SMTP ayarlarını doğrulamak için basit bir test e-postası gönderir.
    Veri kaynaklarına hiç dokunmaz.
    """
    smtp_cfg = config.smtp
    if not smtp_cfg.is_configured:
        return False, f"SMTP ayarları eksik: {', '.join(smtp_cfg.missing_fields())}"

    message = EmailMessage()
    # Başlıklara düz string atanır; kodlamayı policy.default yapar.
    message["Subject"] = "BIST Takip · SMTP test e-postası"
    message["From"] = formataddr((smtp_cfg.mail_from_name, smtp_cfg.mail_from))
    message["To"] = ", ".join(smtp_cfg.mail_to)
    message["Date"] = formatdate(localtime=True)
    message.set_content(
        "Bu bir test mesajıdır.\n\n"
        "Bu e-postayı aldıysan SMTP ayarların doğru çalışıyor demektir.\n"
        "Günlük raporlar aynı adrese gönderilecek.\n",
        charset="utf-8",
    )

    try:
        if smtp_cfg.use_ssl:
            with smtplib.SMTP_SSL(
                smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout,
                context=ssl.create_default_context(),
            ) as server:
                server.login(smtp_cfg.user, smtp_cfg.password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout) as server:
                server.ehlo()
                if smtp_cfg.use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(smtp_cfg.user, smtp_cfg.password)
                server.send_message(message)
        return True, f"Test e-postası gönderildi: {', '.join(smtp_cfg.mail_to)}"
    except Exception as exc:
        return False, f"Test e-postası gönderilemedi: {type(exc).__name__}: {exc}"
