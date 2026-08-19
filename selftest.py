"""
Ağ erişimi gerektirmeyen duman testi (smoke test).

Veritabanı işlemlerini, alarm mantığını, e-posta şablonunu ve biçimlendirme
filtrelerini sahte veriyle uçtan uca doğrular. Kurulumdan hemen sonra
"her şey yerinde mi?" sorusunu ağa çıkmadan yanıtlar.

    python app.py --self-test
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from providers.base import AnalystTarget, NewsItem, PriceData, StockReport
from report import Report, _evaluate_alerts


def _sample_report() -> Report:
    """Şablonun tüm dallarını tetikleyen örnek rapor üretir."""
    now = datetime.now()

    # 1) Tam veri + alarm tetikleyen hisse
    thyao = StockReport(
        symbol="THYAO",
        name="Türk Hava Yolları",
        target_price=300.0,
        alert_pct=3.0,
        price=PriceData(
            symbol="THYAO", last=298.50, open=284.00, high=301.25, low=283.10,
            previous_close=282.75, volume=48_250_000, amount=14_300_000_000,
            updated_at=now, source="borsapy",
        ).fill_derived(),
        analyst=AnalystTarget(
            symbol="THYAO", current=298.50, low=388.0, high=580.0, mean=474.49,
            median=465.0, analyst_count=19, recommendation="AL", upside_percent=58.9,
            summary_counts={"Al": 17, "Tut": 2}, source="borsapy",
        ),
        kap_items=[
            NewsItem(
                title="Payların Geri Alınmasına İlişkin Bildirim",
                url="https://www.kap.org.tr/tr/Bildirim/1530656",
                source="KAP", published=now - timedelta(hours=3),
                summary="TÜRK HAVA YOLLARI A.O. · Özel Durum Açıklaması", kind="kap",
            ),
        ],
        news_items=[
            NewsItem(
                title="Havacılık sektöründe yolcu sayısı rekoru",
                url="https://example.com/haber/1",
                source="Örnek Gazete", published=now - timedelta(hours=6),
                summary="Sektörde taşınan yolcu sayısı geçen yılın aynı dönemine göre arttı. "
                        "Şirket kapasite artırımını sürdürüyor.",
                kind="news",
            ),
        ],
    )

    # 2) Fiyatı düşen, haberi olmayan hisse
    garan = StockReport(
        symbol="GARAN",
        name="Garanti BBVA",
        price=PriceData(
            symbol="GARAN", last=132.40, open=136.00, high=136.80, low=131.90,
            previous_close=136.20, volume=91_400_000, updated_at=now, source="yfinance",
        ).fill_derived(),
    )

    # 3) Veri alınamayan hisse — hata dalını test eder
    broken = StockReport(
        symbol="XXXXX",
        name=None,
        price_error="borsapy: APIError: TradingView error | yfinance: ValueError: veri döndürmedi",
        kap_error="KAP API: ConnectionError: bağlantı kurulamadı",
    )

    for stock in (thyao, garan, broken):
        stock.alerts = _evaluate_alerts(stock)

    return Report(generated_at=now, stocks=[thyao, garan, broken])


def run_self_test(write_preview: bool = True) -> int:
    """
    Duman testini çalıştırır. 0 = başarılı, 1 = başarısız.
    """
    import db
    from mailer import (
        build_subject,
        filter_compact,
        filter_num,
        filter_pct,
        filter_tl,
        render_html,
        render_text,
    )

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        mark = "✓" if condition else "✗"
        print(f"  {mark}  {label}{(' — ' + detail) if detail else ''}")
        if not condition:
            failures.append(label)

    print("Duman testi başlıyor (ağ erişimi gerektirmez)\n")

    # --- 1. Biçimlendirme filtreleri ---
    print("Biçimlendirme filtreleri")
    check("num", filter_num(1234567.5) == "1.234.567,50", filter_num(1234567.5))
    check("tl", filter_tl(298.5) == "298,50 TL", filter_tl(298.5))
    check("pct pozitif", filter_pct(5.567) == "%+5,57", filter_pct(5.567))
    check("pct negatif", filter_pct(-2.79) == "%-2,79", filter_pct(-2.79))
    check("compact", filter_compact(48_250_000) == "48,25 mn", filter_compact(48_250_000))
    check("boş değer", filter_tl(None) == "—")

    # --- 2. Veritabanı ---
    print("\nVeritabanı işlemleri")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test.db"
        db.init_db(test_db)

        ok, _ = db.add_stock("thyao.is", name="Türk Hava Yolları",
                             target_price=300.0, alert_pct=3.0, db_path=test_db)
        check("hisse ekleme + kod normalizasyonu", ok)

        stock = db.get_stock("THYAO", db_path=test_db)
        check("kayıt okuma", stock is not None and stock["symbol"] == "THYAO")
        check("hedef fiyat kaydı", stock is not None and stock["target_price"] == 300.0)

        dup_ok, dup_msg = db.add_stock("THYAO", db_path=test_db)
        check("mükerrer kayıt engelleniyor", not dup_ok, dup_msg)

        db.update_stock("THYAO", target_price=350.0, alert_pct=4.0, db_path=test_db)
        stock = db.get_stock("THYAO", db_path=test_db)
        check("güncelleme", stock is not None and stock["target_price"] == 350.0)

        unseen = db.filter_unseen("THYAO", ["url-a", "url-b"], db_path=test_db)
        check("yeni haberler tespit ediliyor", unseen == {"url-a", "url-b"})
        db.mark_sent("THYAO", ["url-a"], db_path=test_db)
        unseen = db.filter_unseen("THYAO", ["url-a", "url-b"], db_path=test_db)
        check("gönderilen haber tekrar edilmiyor", unseen == {"url-b"})

        run_id = db.start_run("selftest", db_path=test_db)
        db.finish_run(run_id, symbols=3, failures=1, email_sent=True, db_path=test_db)
        runs = db.recent_runs(limit=1, db_path=test_db)
        check("çalıştırma logu", bool(runs) and runs[0]["symbols"] == 3)

        removed, _ = db.remove_stock("THYAO", db_path=test_db)
        check("silme", removed and db.get_stock("THYAO", db_path=test_db) is None)

    # --- 3. Alarm mantığı ---
    print("\nAlarm mantığı")
    report = _sample_report()
    thyao, garan, broken = report.stocks

    check("yüzde eşiği alarmı tetikledi", any("eşiği aşıldı" in a for a in thyao.alerts),
          f"{len(thyao.alerts)} alarm")
    check("hedef fiyat alarmı tetiklendi", any("Hedef" in a for a in thyao.alerts))
    check("eşik altındaki hisse alarm üretmedi", not garan.alerts)
    check("fiyatsız hisse alarm üretmedi", not broken.alerts)

    # --- 4. Rapor özeti ---
    print("\nRapor özeti")
    check("yükselen sayısı", len(report.gainers) == 1, str(len(report.gainers)))
    check("düşen sayısı", len(report.losers) == 1, str(len(report.losers)))
    check("başarısız hisse sayısı", report.failures == 1, str(report.failures))
    check("toplam haber", report.total_news == 2, str(report.total_news))

    # --- 5. Şablon render ---
    print("\nE-posta şablonu")
    try:
        html = render_html(report)
        check("HTML render edildi", len(html) > 2000, f"{len(html)} karakter")
        check("hisse bölümü var", "THYAO" in html and "GARAN" in html)
        check("hata kutusu var", "Fiyat verisi alınamadı" in html)
        check("analist kutusu var", "Analist Hedef Fiyatları" in html)
        check("alarm şeridi var", "Tetiklenen Alarmlar" in html)
        check("KAP linki var", "kap.org.tr/tr/Bildirim/1530656" in html)
    except Exception as exc:
        check("HTML render edildi", False, f"{type(exc).__name__}: {exc}")
        html = ""

    try:
        text = render_text(report)
        check("düz metin render edildi", "THYAO" in text and "[ALARM]" in text,
              f"{len(text)} karakter")
    except Exception as exc:
        check("düz metin render edildi", False, f"{type(exc).__name__}: {exc}")

    try:
        subject = build_subject(report)
        check("konu satırı", "BIST Takip" in subject, subject)
    except Exception as exc:
        check("konu satırı", False, str(exc))

    # --- 5a. Gerçek e-posta nesnesinin kurulması ---
    # Bu blok eskiden testte yoktu: render_html/render_text test ediliyor ama
    # build_message() hiç çağrılmıyordu. Sonuç olarak başlıklara `Header`
    # nesnesi atanmasından kaynaklanan TypeError yalnızca gerçek gönderimde
    # ortaya çıktı. Artık mesaj uçtan uca kuruluyor ve serileştiriliyor.
    print("\nE-posta mesajı kurulumu")
    import os as _os
    from email.message import EmailMessage

    from mailer import build_message

    # build_message config.smtp'yi okur; testte geçici olarak gerçekçi
    # değerler yerleştirip sonra geri alıyoruz.
    from config import SmtpConfig, config as _cfg

    original_smtp = _cfg.smtp
    saved_env = {k: _os.environ.get(k) for k in
                 ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO", "MAIL_FROM", "MAIL_FROM_NAME")}
    try:
        _os.environ.update({
            "SMTP_USER": "test@ornekfirma.com",
            "SMTP_PASSWORD": "gercek-bir-sifre-123",
            "MAIL_TO": "alici@ornekfirma.com",
            "MAIL_FROM": "test@ornekfirma.com",
            "MAIL_FROM_NAME": "BİST Takip Şirketi",   # Türkçe karakterli görünen ad
        })
        _cfg.smtp = SmtpConfig()

        message = build_message(report)
        check("mesaj EmailMessage üretti", isinstance(message, EmailMessage))
        check("multipart (HTML + düz metin)", message.is_multipart())

        raw = message.as_string()
        check("mesaj serileştirilebiliyor", len(raw) > 1000, f"{len(raw)} karakter")
        check("Subject başlığı var", "Subject:" in raw)
        check("From başlığı var", "From:" in raw)
        check("To başlığı doğru", "alici@ornekfirma.com" in raw)
        # Türkçe karakterli görünen ad RFC 2047 ile kodlanmalı
        check("Türkçe görünen ad kodlandı", "=?utf-8?" in raw)
        # Her iki gövde tipi de bulunmalı
        types = {part.get_content_type() for part in message.walk()}
        check("text/plain gövde var", "text/plain" in types, str(sorted(types)))
        check("text/html gövde var", "text/html" in types)

        # SMTP şablon değeri tespiti
        _os.environ["SMTP_USER"] = "ornek@gmail.com"
        _os.environ["SMTP_PASSWORD"] = "xxxx xxxx xxxx xxxx"
        placeholder_cfg = SmtpConfig()
        check(
            "şablon .env değerleri 'yapılandırılmış' sayılmıyor",
            not placeholder_cfg.is_configured,
            ", ".join(placeholder_cfg.missing_fields()),
        )
    except Exception as exc:
        check("mesaj kurulumu", False, f"{type(exc).__name__}: {exc}")
    finally:
        _cfg.smtp = original_smtp
        for key, value in saved_env.items():
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value

    # --- 5a2. Haber "gönderildi" işaretinin zamanlaması ---
    # Eskiden haberler rapor üretilirken, gönderim BAŞARILI OLMADAN önce
    # işaretleniyordu. Gönderim çökünce o günün haberleri kalıcı olarak
    # kayboluyordu (canlı kullanımda yaşandı).
    print("\nHaber gönderim işareti zamanlaması")
    import inspect as _inspect

    from report import build_report as _build_report
    from report import mark_report_sent

    build_params = _inspect.signature(_build_report).parameters
    check(
        "build_report artık işaretleme yapmıyor",
        "mark_as_sent" not in build_params,
        ", ".join(build_params),
    )
    check("mark_report_sent ayrı fonksiyon olarak var", callable(mark_report_sent))

    # runner.run_once içinde işaretleme, gönderim başarısına bağlı olmalı
    import runner as _runner

    runner_src = _inspect.getsource(_runner.run_once)
    mark_pos = runner_src.find("mark_report_sent")
    send_pos = runner_src.find("send_report")
    check(
        "işaretleme gönderimden SONRA çağrılıyor",
        mark_pos > send_pos > -1,
        f"send@{send_pos} < mark@{mark_pos}",
    )
    check(
        "işaretleme email_sent koşuluna bağlı",
        "if email_sent" in runner_src,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        news_db = Path(tmpdir) / "news.db"
        db.init_db(news_db)
        keys = ["https://ornek/1", "https://ornek/2"]
        check(
            "işaretlenmemiş haberler 'yeni' sayılıyor",
            db.filter_unseen("THYAO", keys, db_path=news_db) == set(keys),
        )
        db.mark_sent("THYAO", keys, db_path=news_db)
        check(
            "işaretlendikten sonra tekrar gönderilmiyor",
            db.filter_unseen("THYAO", keys, db_path=news_db) == set(),
        )

    # --- 5b. Sağlayıcı veri işleme regresyonları ---
    print("\nFiyat alanı türetme")
    # borsapy önceki kapanışı `prev_close` adıyla döndürür; alan boş geldiğinde
    # son fiyat ve değişimden geri hesaplanmalı (canlı testte yakalandı).
    derived = PriceData(symbol="ASELS", last=398.75, change=3.00, change_percent=0.76).fill_derived()
    check(
        "önceki kapanış değişimden türetildi",
        derived.previous_close is not None and abs(derived.previous_close - 395.75) < 0.01,
        f"{derived.previous_close}",
    )
    only_pct = PriceData(symbol="X", last=110.0, change_percent=10.0).fill_derived()
    check(
        "önceki kapanış yüzdeden türetildi",
        only_pct.previous_close is not None and abs(only_pct.previous_close - 100.0) < 0.01,
        f"{only_pct.previous_close}",
    )
    reverse = PriceData(symbol="Y", last=132.40, previous_close=136.20).fill_derived()
    check(
        "ters yön hâlâ çalışıyor",
        reverse.change is not None and abs(reverse.change + 3.80) < 0.01,
        f"{reverse.change}",
    )

    print("\nHaber özeti tekrar filtresi")
    from providers.news import is_redundant_summary

    news_title = "ASELSAN (ASELS) 14 Ağustos Cuma 2026 Günlük Teknik Analiz"
    check(
        "başlık + kaynak tekrarı elendi",
        is_redundant_summary(f"{news_title} Mynet Finans", news_title, "Mynet Finans"),
    )
    check("birebir aynı özet elendi", is_redundant_summary(news_title, news_title, ""))
    check("boş özet elendi", is_redundant_summary("", news_title, ""))
    check(
        "gerçek özet korundu",
        not is_redundant_summary(
            "Şirket ikinci çeyrekte 12 milyar TL ciro açıkladı ve yeni savunma "
            "ihalesini kazandığını duyurdu.",
            news_title,
            "Mynet Finans",
        ),
    )

    # --- 5c. Windows başlatıcı dosyalarının kodlaması ---
    # Windows PowerShell 5.1, BOM'suz bir .ps1 dosyasını sistem kod sayfasıyla
    # (Türkçe Windows'ta cp1254) okur. Türkçe karakterler bozulur ve betik
    # "Missing closing '}'" hatasıyla hiç çalışmaz. Bu canlı testte yakalandı.
    print("\nWindows başlatıcı dosya kodlaması")
    from config import BASE_DIR

    utf8_bom = b"\xef\xbb\xbf"

    for ps_name in ("scripts/launcher.ps1", "scripts/create-shortcut.ps1"):
        ps_path = BASE_DIR / ps_name
        if not ps_path.exists():
            check(f"{ps_name} mevcut", False, "dosya yok")
            continue
        raw = ps_path.read_bytes()
        check(f"{ps_name} UTF-8 BOM ile başlıyor", raw.startswith(utf8_bom))
        # BOM'dan sonrası geçerli UTF-8 olmalı
        try:
            decoded = raw[len(utf8_bom):].decode("utf-8")
            balanced = decoded.count("{") == decoded.count("}")
            check(f"{ps_name} süslü parantezleri dengeli", balanced)
        except UnicodeDecodeError as exc:
            check(f"{ps_name} geçerli UTF-8", False, str(exc))

    # .bat dosyaları BOM İÇERMEMELİ — cmd.exe BOM'u komut sanıp hata verir.
    for bat_name in ("BIST-Takip-Baslat.bat", "Masaustu-Kisayol-Olustur.bat"):
        bat_path = BASE_DIR / bat_name
        if not bat_path.exists():
            check(f"{bat_name} mevcut", False, "dosya yok")
            continue
        raw = bat_path.read_bytes()
        check(f"{bat_name} BOM içermiyor", not raw.startswith(utf8_bom))
        check(
            f"{bat_name} saf ASCII",
            all(byte < 128 for byte in raw),
            "ASCII dışı bayt var" if any(b >= 128 for b in raw) else "",
        )

    # --- 6. Güvenlik regresyon testleri ---
    print("\nGüvenlik: URL şeması doğrulaması (O-3)")
    from security import is_safe_url, sanitize_url

    for bad in (
        "javascript:alert(document.domain)",
        "JavaScript:alert(1)",
        "  javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "java\tscript:alert(1)",
        "",
        None,
    ):
        check(f"reddedildi: {str(bad)[:32]!r}", not is_safe_url(bad))
    for good in ("https://www.kap.org.tr/tr/Bildirim/123", "http://example.com/haber"):
        check(f"kabul edildi: {good[:38]!r}", is_safe_url(good))
    check("sanitize_url güvensizi boşaltıyor", sanitize_url("javascript:alert(1)") == "")

    print("\nGüvenlik: hisse kodu biçim doğrulaması (D-2)")
    for bad_symbol in ("", "AB", "ABCDEFG", "A" * 20):
        ok_fmt, _ = db.validate_symbol_format(db.normalize_symbol(bad_symbol))
        check(f"reddedildi: {bad_symbol[:12]!r}", not ok_fmt)
    for good_symbol in ("THYAO", "GARAN", "ISCTR"):
        ok_fmt, _ = db.validate_symbol_format(db.normalize_symbol(good_symbol))
        check(f"kabul edildi: {good_symbol!r}", ok_fmt)
    check(
        "tehlikeli karakterler temizleniyor",
        db.normalize_symbol("../../etc/passwd") == "ETCPASSWD",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "sec.db"
        db.init_db(test_db)
        added_ok, _ = db.add_stock("AB", db_path=test_db)
        check("kısa kod DB'ye eklenemiyor", not added_ok)
        added_ok, _ = db.add_stock("A" * 20, db_path=test_db)
        check("aşırı uzun kod DB'ye eklenemiyor", not added_ok)

    # `<script>` normalize edilince `SCRIPT` olur ve biçim kuralına uyar.
    # Bu bilinçli bir tasarım: tehlikeli karakterler yok edilmiş olur.
    # Anlamsız kodları elemek canlı BIST listesi doğrulamasının işidir.
    from providers.symbols import is_valid_symbol

    check(
        "zararlı yük zararsızlaştırıldı",
        db.normalize_symbol("<script>") == "SCRIPT",
        db.normalize_symbol("<script>"),
    )
    exists_ok, exists_reason = is_valid_symbol("SCRIPT")
    check(
        "sahte kod ya reddedildi ya da doğrulanamadı olarak işaretlendi",
        (not exists_ok) or exists_reason == "doğrulanamadı",
        exists_reason or "canlı listede yok",
    )
    thyao_ok, _ = is_valid_symbol("THYAO")
    check("gerçek kod kabul edildi", thyao_ok)

    print("\nGüvenlik: e-posta adresi doğrulaması (D-3)")
    from config import is_valid_email

    for bad_mail in ("iyi@x.com\nBcc: kotu@saldirgan.com", "@x.com", "a@b", "a b@x.com", ""):
        check(f"reddedildi: {bad_mail[:28]!r}", not is_valid_email(bad_mail))
    check("kabul edildi: 'ornek@gmail.com'", is_valid_email("ornek@gmail.com"))

    print("\nGüvenlik: hata metni temizleme (O-5)")
    from report import _safe_error_text

    cleaned = _safe_error_text(FileNotFoundError("/home/kullanici/gizli/proje/data/bist.db yok"))
    check("POSIX yolu gizlendi", "/home/kullanici" not in cleaned, cleaned)
    cleaned_win = _safe_error_text(r"C:\Users\Yagiz\proje\.env okunamadı")
    check("Windows yolu gizlendi", r"C:\Users" not in cleaned_win, cleaned_win)
    check("uzunluk sınırlandı", len(_safe_error_text("x" * 900)) <= 190)

    print("\nGüvenlik: secret key (O-1)")
    from config import config as cfg

    check("sabit varsayılan anahtar kaldırıldı", cfg.secret_key != "bist-takip-dev-key")
    key1 = cfg.resolve_secret_key()
    check("anahtar üretiliyor", bool(key1) and len(key1) >= 32, f"{len(key1)} karakter")
    check("anahtar kalıcı (aynı değer)", cfg.resolve_secret_key() == key1)

    print("\nGüvenlik: şablonda güvensiz URL bağlantıya dönüşmüyor (O-3)")
    unsafe_report = Report(
        generated_at=datetime.now(),
        stocks=[
            StockReport(
                symbol="TEST",
                price=PriceData(
                    symbol="TEST", last=10.0, previous_close=10.0, source="test"
                ).fill_derived(),
                news_items=[
                    NewsItem(
                        title="Zararsız başlık",
                        url="",  # providers katmanı güvensiz URL'i boşaltmış olur
                        source="Test",
                        published=datetime.now(),
                    )
                ],
            )
        ],
    )
    unsafe_html = render_html(unsafe_report)
    check("boş URL <a href> üretmiyor", 'href=""' not in unsafe_html)
    check("başlık düz metin olarak var", "Zararsız başlık" in unsafe_html)

    # --- 7. Önizleme dosyası ---
    if write_preview and html:
        from config import BASE_DIR

        outbox = BASE_DIR / "outbox"
        outbox.mkdir(exist_ok=True)
        preview_path = outbox / "ornek-rapor.html"
        preview_path.write_text(html, encoding="utf-8")
        print(f"\nÖrnek rapor yazıldı: {preview_path}")
        print("Tarayıcıda açarak e-posta düzenini görebilirsin.")

    # --- Sonuç ---
    print()
    if failures:
        print(f"✗ {len(failures)} test başarısız: {', '.join(failures)}")
        return 1
    print("✓ Tüm testler başarılı.")
    return 0
