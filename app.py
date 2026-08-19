#!/usr/bin/env python3
"""
BIST Hisse Takip ve Bilgilendirme Uygulaması — giriş noktası.

Kullanım örnekleri
------------------
  python app.py --serve                 Web arayüzünü başlat (varsayılan)
  python app.py --run-now               Raporu şimdi üret ve e-posta gönder
  python app.py --run-now --dry-run     Gönderme, HTML önizlemeyi outbox/'a yaz
  python app.py --schedule              Zamanlayıcıyı arka planda çalıştır
  python app.py --serve --schedule      İkisini birlikte çalıştır
  python app.py --check-sources         Veri kaynaklarına erişimi test et
  python app.py --self-test             Ağ gerektirmeyen duman testi
  python app.py --test-email            SMTP ayarlarını test et
  python app.py --add THYAO GARAN       Komut satırından hisse ekle
  python app.py --remove THYAO          Hisse çıkar
  python app.py --list                  Takip listesini yazdır
"""

from __future__ import annotations

import argparse
import sys
import threading

import db
from config import config
from logging_setup import get_logger, setup_logging

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Alt komutlar
# ---------------------------------------------------------------------------

def cmd_list() -> int:
    """Takip listesini tabloya benzer biçimde yazdırır."""
    stocks = db.list_stocks()
    if not stocks:
        print("Takip listesi boş. `python app.py --add THYAO` ile ekleyebilirsin.")
        return 0

    print(f"{'KOD':<8} {'DURUM':<7} {'HEDEF':>10} {'ALARM%':>8}  ŞİRKET")
    print("-" * 62)
    for stock in stocks:
        target = f"{stock['target_price']:.2f}" if stock["target_price"] else "—"
        alert = f"{stock['alert_pct']:.2f}" if stock["alert_pct"] else "—"
        status = "aktif" if stock["active"] else "pasif"
        print(f"{stock['symbol']:<8} {status:<7} {target:>10} {alert:>8}  {stock['name'] or ''}")
    print(f"\nToplam {len(stocks)} hisse.")
    return 0


def cmd_add(symbols: list[str], target: float | None, alert: float | None) -> int:
    """Komut satırından hisse ekler."""
    from providers import symbols as symbol_provider

    exit_code = 0
    for raw in symbols:
        symbol = db.normalize_symbol(raw)

        # D-2: web arayüzüyle aynı iki katmanlı doğrulama
        ok_format, reason = db.validate_symbol_format(symbol)
        if not ok_format:
            print(f"! {raw}: geçersiz kod ({reason})")
            exit_code = 1
            continue
        try:
            ok_exists, exists_reason = symbol_provider.is_valid_symbol(symbol)
            if not ok_exists:
                print(f"! {symbol}: {exists_reason}")
                exit_code = 1
                continue
        except Exception:
            pass

        try:
            name = symbol_provider.resolve_name(symbol)
        except Exception:
            name = None
        ok, msg = db.add_stock(
            symbol,
            name=name,
            target_price=target if len(symbols) == 1 else None,
            alert_pct=alert,
        )
        print(("✓ " if ok else "! ") + msg)
        if not ok:
            exit_code = 1
    return exit_code


def cmd_remove(symbols: list[str]) -> int:
    """Komut satırından hisse çıkarır."""
    exit_code = 0
    for raw in symbols:
        ok, msg = db.remove_stock(raw)
        print(("✓ " if ok else "! ") + msg)
        if not ok:
            exit_code = 1
    return exit_code


def cmd_check_sources() -> int:
    """
    Her veri kaynağını tek tek dener ve sonucu raporlar.

    Kurulum sonrası "hangi kaynak çalışıyor?" sorusunu yanıtlamak için.
    Ağ kısıtı olan ortamlarda hangi sağlayıcının kullanılabileceğini gösterir.
    """
    from providers import kap as kap_provider
    from providers import news as news_provider
    from providers import prices as price_provider
    from providers import symbols as symbol_provider
    from providers.analysts import get_targets

    test_symbol = "THYAO"
    print(f"Veri kaynakları test ediliyor (örnek hisse: {test_symbol})\n")
    results: list[tuple[str, bool, str]] = []

    # 1) Fiyat sağlayıcıları — her biri ayrı ayrı
    for provider_name in ("borsapy", "yfinance"):
        result = price_provider.get_price(test_symbol, provider_order=[provider_name])
        if result.ok and result.data:
            detail = f"son fiyat {result.data.last:.2f} TL"
        else:
            detail = (result.error or "bilinmeyen hata")[:150]
        results.append((f"Fiyat · {provider_name}", result.ok, detail))

    # 2) KAP
    kap_result = kap_provider.get_disclosures(test_symbol)
    results.append((
        "KAP bildirimleri",
        kap_result.ok,
        f"{len(kap_result.data or [])} bildirim ({kap_result.source})"
        if kap_result.ok else (kap_result.error or "")[:150],
    ))

    # 3) Google News
    news_result = news_provider.get_news(test_symbol, company_name="Türk Hava Yolları")
    results.append((
        "Google News RSS",
        news_result.ok,
        f"{len(news_result.data or [])} haber" if news_result.ok else (news_result.error or "")[:150],
    ))

    # 4) Analist hedefleri
    analyst_result = get_targets(test_symbol)
    results.append((
        "Analist hedef fiyatları",
        analyst_result.ok,
        f"ortalama {analyst_result.data.mean}" if analyst_result.ok and analyst_result.data
        else (analyst_result.error or "")[:150],
    ))

    # 5) Şirket adı çözümlemesi
    try:
        name = symbol_provider.resolve_name(test_symbol)
        results.append(("Şirket adı çözümleme", bool(name), name or "bulunamadı"))
    except Exception as exc:
        results.append(("Şirket adı çözümleme", False, str(exc)[:150]))

    # 6) SMTP yapılandırması (bağlanmadan, sadece alan kontrolü)
    smtp_ok = config.smtp.is_configured
    results.append((
        "SMTP yapılandırması",
        smtp_ok,
        f"{config.smtp.host}:{config.smtp.port} → {', '.join(config.smtp.mail_to)}"
        if smtp_ok else f"eksik alanlar: {', '.join(config.smtp.missing_fields())}",
    ))

    for label, ok, detail in results:
        mark = "✓" if ok else "✗"
        print(f"  {mark}  {label:<26} {detail}")

    critical_ok = any(ok for label, ok, _ in results if label.startswith("Fiyat"))
    print()
    if critical_ok:
        print("Fiyat verisi alınabiliyor — uygulama çalışabilir durumda.")
    else:
        print("UYARI: Hiçbir fiyat sağlayıcısına erişilemedi. Ağ/proxy ayarlarını kontrol et.")
    return 0 if critical_ok else 1


def cmd_diagnose_kap(days: int = 3) -> int:
    """
    KAP eşleştirmesini ayrıntılı test eder.

    "Bugün bildirim yok" ile "eşleştirme çalışmıyor" arasındaki farkı ayırt
    etmek için: ham API yanıtındaki alanları, ilgili hisse kodlarının nasıl
    göründüğünü ve takip listesindeki her kod için kaç eşleşme bulunduğunu
    gösterir.
    """
    from providers import kap as kap_provider

    print(f"KAP tanılaması — son {days} günün bildirimleri\n")

    try:
        rows = kap_provider.load_disclosures(lookback_days=days)
    except Exception as exc:
        print(f"✗ KAP API'ye ulaşılamadı: {type(exc).__name__}: {exc}")
        return 1

    print(f"✓ API yanıt verdi — toplam {len(rows)} bildirim indirildi\n")

    if not rows:
        print("  Yanıt boş. Tarih aralığını --kap-days ile genişletmeyi dene.")
        return 1

    # 1) Yanıttaki alan adları — API değişmişse burada görünür.
    sample = rows[0]
    print("Yanıttaki alanlar:")
    print("  " + ", ".join(sorted(sample.keys())))
    print()

    # 2) İlgili hisse alanı nasıl geliyor?
    print("İlk 3 bildirimin ilgili hisse alanı:")
    for row in rows[:3]:
        raw = row.get("relatedStocks", "<alan yok>")
        parsed = kap_provider._related_symbols(row)
        print(f"  ham={raw!r}")
        print(f"    → ayrıştırılan: {sorted(parsed) or '(boş)'}")
    print()

    # 3) Yanıtta geçen tüm hisse kodları
    all_symbols: set[str] = set()
    for row in rows:
        all_symbols |= kap_provider._related_symbols(row)
    print(f"Yanıtta geçen benzersiz hisse kodu sayısı: {len(all_symbols)}")
    if all_symbols:
        print("  Örnek: " + ", ".join(sorted(all_symbols)[:15]))
    print()

    # 4) Takip listesindeki her kod için eşleşme
    watchlist = [s["symbol"] for s in db.list_stocks(only_active=True)]
    if not watchlist:
        watchlist = ["THYAO", "GARAN", "ASELS", "TUPRS"]
        print("(Takip listesi boş — örnek kodlarla test ediliyor)\n")

    print("Takip listesi eşleşmeleri:")
    total_matches = 0
    for symbol in watchlist:
        matched = [r for r in rows if symbol in kap_provider._related_symbols(r)]
        total_matches += len(matched)
        mark = "✓" if matched else "·"
        print(f"  {mark} {symbol:<8} {len(matched)} bildirim")
        for row in matched[:2]:
            subject = (row.get("subject") or row.get("kapTitle") or "?")[:60]
            print(f"        {row.get('publishDate', '?')} — {subject}")

    print()
    if total_matches:
        print("Sonuç: KAP eşleştirmesi çalışıyor.")
        return 0

    if all_symbols:
        print(
            "Sonuç: API çalışıyor ve hisse kodları ayrıştırılıyor, ancak takip\n"
            "listendeki kodlar bu aralıkta bildirim yayımlamamış. Bu normaldir —\n"
            "her şirket her gün bildirim yapmaz. --kap-days ile aralığı genişlet."
        )
        return 0

    print(
        "Sonuç: SORUN VAR — bildirimler indiriliyor ama hiçbirinden hisse kodu\n"
        "ayrıştırılamıyor. KAP API alan adlarını değiştirmiş olabilir.\n"
        "Yukarıdaki 'Yanıttaki alanlar' listesini paylaş."
    )
    return 1


def cmd_reset_news(symbols: list[str] | None = None) -> int:
    """
    "Gönderildi" işaretlerini temizler; haberler bir sonraki raporda tekrar görünür.

    Başarısız bir gönderimden sonra haberler yanlışlıkla işaretlenmiş olabilir
    (eski sürümdeki hata). Bu komut o kayıtları siler.
    """
    with db.get_conn() as conn:
        if symbols:
            cleaned = [db.normalize_symbol(s) for s in symbols]
            placeholders = ",".join("?" * len(cleaned))
            cur = conn.execute(
                f"DELETE FROM sent_news WHERE symbol IN ({placeholders})", cleaned
            )
            scope = ", ".join(cleaned)
        else:
            cur = conn.execute("DELETE FROM sent_news")
            scope = "tüm hisseler"

    print(f"✓ {cur.rowcount} kayıt temizlendi ({scope}).")
    print("  Bu haberler bir sonraki raporda tekrar görünecek.")
    return 0


def cmd_check_secrets() -> int:
    """
    Kimlik bilgisi güvenliği denetimi.

    Şifrenin nerede durduğunu, kimlerin okuyabildiğini ve başka bir dosyaya
    sızıp sızmadığını kontrol eder. Şifrenin kendisi hiçbir zaman ekrana
    yazılmaz — yalnızca uzunluğu ve nerede bulunduğu bildirilir.
    """
    import re

    from config import BASE_DIR, describe_permissions

    print("Kimlik bilgisi güvenlik denetimi\n")
    problems = 0

    env_file = BASE_DIR / ".env"
    password = config.smtp.password
    user = config.smtp.user

    # --- 1. Şifre nerede? ---
    print("1) Şifrenin konumu")
    if not env_file.exists():
        print("   ✗ .env dosyası yok")
        return 1
    print(f"   ✓ Yalnızca .env dosyasında ({len(password)} karakter, "
          f"boşluksuz {len(password.replace(' ', ''))})")
    print(f"   ✓ Kod içinde gömülü şifre yok (tüm değerler ortam değişkeninden)")

    # --- 2. Dosya izinleri ---
    # Fazla açık bulunan dosyalar önce sıkılaştırılır, sonra tekrar okunur.
    print("\n2) Dosya izinleri")
    from config import _harden_permissions, is_permission_too_open

    for label, path in (
        (".env", env_file),
        ("data/secret_key", config.db_path.parent / "secret_key"),
        ("data/bist.db", config.db_path),
    ):
        if not path.exists():
            print(f"   ·  {label:16} (henüz oluşmamış)")
            continue

        if is_permission_too_open(path):
            _harden_permissions(path)          # düzeltmeyi dene
            if is_permission_too_open(path):   # hâlâ açıksa gerçek sorun
                print(f"   ✗  {label:16} {describe_permissions(path)}")
                problems += 1
                continue
            print(f"   ✓  {label:16} {describe_permissions(path)}  (düzeltildi)")
            continue

        print(f"   ✓  {label:16} {describe_permissions(path)}")

    # --- 3. Sızıntı taraması ---
    print("\n3) Şifre başka dosyalara sızmış mı?")
    variants = {password, password.replace(" ", "")}
    variants = {v for v in variants if len(v) >= 8}

    scan_targets: list[Path] = []
    for folder, pattern in (
        (BASE_DIR / "logs", "*.log*"),
        (BASE_DIR / "outbox", "*.html"),
        (BASE_DIR / "data", "*.db"),
    ):
        if folder.exists():
            scan_targets.extend(folder.glob(pattern))

    if not scan_targets:
        print("   (taranacak dosya yok)")
    for target in scan_targets:
        try:
            text = target.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        leaked = variants and any(v in text for v in variants)
        mark = "✗ SIZMIŞ" if leaked else "✓ temiz"
        if leaked:
            problems += 1
        print(f"   {mark}  {target.relative_to(BASE_DIR)}")

    # --- 4. Sürüm kontrolü riski ---
    print("\n4) Sürüm kontrolü")
    if (BASE_DIR / ".git").exists():
        gitignore = BASE_DIR / ".gitignore"
        ignored = gitignore.exists() and re.search(
            r"(?m)^\s*\.env\s*$", gitignore.read_text(encoding="utf-8", errors="replace")
        )
        if ignored:
            print("   ✓ Git deposu var, .env .gitignore'da hariç tutulmuş")
        else:
            print("   ✗ Git deposu var ama .env hariç tutulmamış — commit riski!")
            problems += 1
    else:
        print("   ✓ Git deposu yok, yanlışlıkla commit riski yok")

    # --- 5. Aktarım güvenliği ---
    print("\n5) Aktarım güvenliği")
    if config.smtp.use_ssl:
        print(f"   ✓ Doğrudan SSL ({config.smtp.host}:{config.smtp.port})")
    elif config.smtp.use_tls:
        print(f"   ✓ STARTTLS ile şifreli ({config.smtp.host}:{config.smtp.port})")
    else:
        print("   ✗ ŞİFRELEME YOK — şifre düz metin gidiyor!")
        problems += 1
    print("   ✓ Sunucu sertifikası ve adı doğrulanıyor (ssl.create_default_context)")

    # --- Sonuç ---
    print()
    if problems:
        print(f"✗ {problems} sorun bulundu (yukarıda ✗ ile işaretli).")
        return 1
    print("✓ Sorun bulunmadı.")
    print(f"  Şifreyi iptal etmek istersen: https://myaccount.google.com/apppasswords")
    return 0


def cmd_smtp_status() -> int:
    """
    SMTP yapılandırılmış mı? Çıkış kodu: 0 = evet, 1 = hayır.

    Başlatıcı betiği bu komutu çağırır; böylece "yapılandırılmış mı"
    kararı tek yerde (config.SmtpConfig) verilir ve iki ayrı yerde
    yazılmış kurallar birbiriyle çelişmez.
    """
    if config.smtp.is_configured:
        print("SMTP hazır → " + ", ".join(config.smtp.mail_to))
        return 0
    print("SMTP eksik: " + ", ".join(config.smtp.missing_fields()))
    return 1


def cmd_test_email() -> int:
    """SMTP ayarlarını test e-postasıyla doğrular."""
    from mailer import send_test_email

    ok, msg = send_test_email()
    print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


def cmd_run_now(args: argparse.Namespace) -> int:
    """Raporu hemen üretir."""
    from runner import run_once

    result = run_once(
        trigger="manual",
        dry_run=args.dry_run,
        symbols=args.symbols or None,
        force=args.force,
        send_email=not args.no_email,
    )
    print(("✓ " if result.ok else "✗ ") + result.summary)
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# Argüman ayrıştırma
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="BIST hisse takip ve günlük e-posta bilgilendirme uygulaması.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_argument_group("çalışma modu")
    mode.add_argument("--serve", action="store_true", help="Web arayüzünü başlat")
    mode.add_argument("--schedule", action="store_true", help="Zamanlayıcıyı çalıştır")
    mode.add_argument("--run-now", action="store_true", help="Raporu şimdi üret ve gönder")

    options = parser.add_argument_group("çalıştırma seçenekleri")
    options.add_argument("--dry-run", action="store_true",
                         help="E-posta gönderme; HTML'i outbox/ klasörüne yaz")
    options.add_argument("--no-email", action="store_true",
                         help="Raporu üret ama gönderme")
    options.add_argument("--force", action="store_true",
                         help="Hafta sonu atlama kuralını yok say")
    options.add_argument("--symbols", nargs="+", metavar="KOD",
                         help="Sadece bu hisseler için çalıştır")
    options.add_argument("--run-immediately", action="store_true",
                         help="--schedule ile birlikte: başlangıçta bir kez çalıştır")

    manage = parser.add_argument_group("liste yönetimi")
    manage.add_argument("--add", nargs="+", metavar="KOD", help="Takip listesine hisse ekle")
    manage.add_argument("--remove", nargs="+", metavar="KOD", help="Takip listesinden çıkar")
    manage.add_argument("--list", action="store_true", help="Takip listesini yazdır")
    manage.add_argument("--reset-news", nargs="*", metavar="KOD",
                        help="Haber 'gönderildi' işaretlerini temizle "
                             "(kod verilmezse tümü)")
    manage.add_argument("--target", type=float, help="--add ile: hedef fiyat")
    manage.add_argument("--alert", type=float, help="--add ile: alarm eşiği (%%)")

    diag = parser.add_argument_group("teşhis")
    diag.add_argument("--check-sources", action="store_true", help="Veri kaynaklarını test et")
    diag.add_argument("--self-test", action="store_true",
                      help="Ağ gerektirmeyen duman testi (DB, alarm, şablon)")
    diag.add_argument("--diagnose-kap", action="store_true",
                      help="KAP bildirim eşleştirmesini ayrıntılı test et")
    diag.add_argument("--kap-days", type=int, default=3,
                      help="--diagnose-kap ile: kaç gün geriye bakılsın (varsayılan 3)")
    diag.add_argument("--test-email", action="store_true", help="SMTP ayarlarını test et")
    diag.add_argument("--smtp-status", action="store_true",
                      help="SMTP yapılandırılmış mı (çıkış kodu 0=evet, 1=hayır)")
    diag.add_argument("--check-secrets", action="store_true",
                      help="Kimlik bilgisi güvenlik denetimi (izinler, sızıntı, TLS)")
    diag.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                      help="Log seviyesini geçici olarak değiştir")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    setup_logging(args.log_level)
    config.ensure_dirs()
    db.init_db()

    # --- Liste yönetimi (tek seferlik komutlar) ---
    if args.list:
        return cmd_list()
    if args.add:
        return cmd_add(args.add, args.target, args.alert)
    if args.remove:
        return cmd_remove(args.remove)
    if args.reset_news is not None:
        return cmd_reset_news(args.reset_news or None)

    # --- Teşhis ---
    if args.self_test:
        from selftest import run_self_test

        return run_self_test()
    if args.diagnose_kap:
        return cmd_diagnose_kap(days=args.kap_days)
    if args.check_sources:
        return cmd_check_sources()
    if args.check_secrets:
        return cmd_check_secrets()
    if args.smtp_status:
        return cmd_smtp_status()
    if args.test_email:
        return cmd_test_email()

    # --- Rapor çalıştırma ---
    if args.run_now:
        return cmd_run_now(args)

    # --- Uzun süreli modlar ---
    from scheduler import run_scheduler
    from webapp import run_web

    # Hiçbir mod verilmediyse web arayüzünü aç (en sık kullanılan senaryo).
    serve = args.serve or not args.schedule

    if serve and args.schedule:
        # Zamanlayıcıyı arka plan thread'inde, web sunucusunu ön planda çalıştır.
        thread = threading.Thread(
            target=run_scheduler, kwargs={"run_immediately": args.run_immediately}, daemon=True
        )
        thread.start()
        log.info("Zamanlayıcı arka planda başlatıldı.")
        run_web()
        return 0

    if args.schedule:
        run_scheduler(run_immediately=args.run_immediately)
        return 0

    run_web()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nDurduruldu.")
        sys.exit(130)
