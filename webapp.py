"""
Flask web arayüzü — takip listesi yönetimi ve manuel çalıştırma.

Yalnızca localhost'ta çalışması hedeflenmiştir (kimlik doğrulama yoktur).
FLASK_HOST'u 0.0.0.0 yapacaksan önüne bir kimlik doğrulama katmanı koy.

Uzun süren rapor çalıştırmaları arka plan thread'inde yapılır; sayfa
durumu periyodik olarak yeniler, böylece tarayıcı zaman aşımına uğramaz.
"""

from __future__ import annotations

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from typing import Any

from flask import (
    Flask,
    Response,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException

import db
import security
from config import config
from logging_setup import get_logger
from mailer import render_html, send_test_email
from providers import history as history_provider
from providers import kap as kap_provider
from providers import news as news_provider
from providers import prices as price_provider
from providers import symbols as symbol_provider
from runner import run_once

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Basit arka plan iş yöneticisi (aynı anda tek iş)
# ---------------------------------------------------------------------------

class JobManager:
    """Rapor çalıştırmalarını arka planda yürütür ve durumunu tutar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status: str = "idle"          # idle | running | done | error
        self.message: str = ""
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.last_html: str | None = None   # son raporun HTML önizlemesi

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, **kwargs: Any) -> tuple[bool, str]:
        """Yeni bir çalıştırma başlatır. Zaten çalışıyorsa reddeder."""
        with self._lock:
            if self.is_running:
                return False, "Zaten bir çalıştırma devam ediyor."
            self.status = "running"
            self.message = "Veriler toplanıyor…"
            self.started_at = datetime.now()
            self.finished_at = None
            self._thread = threading.Thread(target=self._work, kwargs=kwargs, daemon=True)
            self._thread.start()
        return True, "Çalıştırma başlatıldı."

    def _work(self, **kwargs: Any) -> None:
        try:
            result = run_once(trigger="web", **kwargs)
            if result.report is not None:
                try:
                    self.last_html = render_html(result.report)
                except Exception as exc:
                    log.warning("Önizleme HTML üretilemedi: %s", exc)
            self.status = "done" if result.ok else "error"
            self.message = result.summary
        except Exception as exc:
            log.exception("Arka plan çalıştırması çöktü")
            self.status = "error"
            self.message = f"{type(exc).__name__}: {exc}"
        finally:
            self.finished_at = datetime.now()

    def snapshot(self) -> dict[str, Any]:
        """Durumu JSON'lanabilir sözlük olarak döndürür."""
        return {
            "status": "running" if self.is_running else self.status,
            "message": self.message,
            "started_at": self.started_at.strftime("%H:%M:%S") if self.started_at else None,
            "finished_at": self.finished_at.strftime("%H:%M:%S") if self.finished_at else None,
            "has_preview": self.last_html is not None,
        }


jobs = JobManager()


# ---------------------------------------------------------------------------
# Form yardımcıları
# ---------------------------------------------------------------------------

def _parse_optional_float(raw: str | None) -> float | None:
    """
    Boş girdiyi None'a, virgüllü ondalığı float'a çevirir.

    Üst sınır kontrolü de yapar: `inf`/`nan` ve absürt büyük değerler
    hesaplamaları ve şablon biçimlendirmesini bozabilir.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", ".")[:32]
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    # NaN ve sonsuzluk elenir (NaN != NaN)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if not (0 < value < 1_000_000_000):
        return None
    return value


# Girdi uzunluğu üst sınırları — aşırı uzun değerlerin DB'ye ve e-postaya
# girmesini engeller.
MAX_NOTES_CHARS = 200
MAX_SYMBOL_INPUT_CHARS = 400
MAX_SYMBOLS_PER_REQUEST = 25


def _clean_notes(raw: str | None) -> str | None:
    """Not alanını temizler: kontrol karakterlerini atar, uzunluğu sınırlar."""
    if not raw:
        return None
    cleaned = "".join(ch for ch in raw if ord(ch) >= 0x20 or ch in "\t")
    cleaned = cleaned.strip()[:MAX_NOTES_CHARS]
    return cleaned or None


# ---------------------------------------------------------------------------
# Uygulama fabrikası
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    """
    Flask uygulamasını kurar.

    Güvenlik katmanı (`security.init_app`) Host doğrulaması, CSRF, opsiyonel
    kimlik doğrulama ve güvenlik başlıklarını devreye alır.
    """
    # K-2: Dışarıya açık + kimlik doğrulamasız yapılandırmayı reddet.
    fatal = config.fatal_security_errors()
    if fatal:
        raise RuntimeError(
            "Güvenlik yapılandırması geçersiz, uygulama başlatılmadı:\n  - "
            + "\n  - ".join(fatal)
        )

    app = Flask(__name__)

    # O-1: sabit varsayılan yerine üretilen/kalıcı anahtar.
    # Uyarılar bundan SONRA toplanır — zayıf anahtar tespiti burada yapılıyor.
    app.secret_key = config.resolve_secret_key()

    for warning in config.security_warnings():
        log.warning("GÜVENLİK: %s", warning)

    security.init_app(app)
    db.init_db()

    # ------------------------------ Sayfalar ------------------------------

    @app.route("/")
    def index() -> str:
        stocks = db.list_stocks()
        runs = db.recent_runs(limit=8)

        return render_template(
            "index.html",
            stocks=stocks,
            runs=runs,
            job=jobs.snapshot(),
            config=config,
            smtp_ok=config.smtp.is_configured,
            smtp_missing=config.smtp.missing_fields(),
            default_alert_pct=config.default_alert_pct,
        )

    @app.route("/add", methods=["POST"])
    def add() -> Response:
        raw_symbols = (request.form.get("symbol", "") or "")[:MAX_SYMBOL_INPUT_CHARS]
        target = _parse_optional_float(request.form.get("target_price"))
        alert = _parse_optional_float(request.form.get("alert_pct"))
        notes = _clean_notes(request.form.get("notes"))

        # Tek seferde birden fazla kod eklenebilsin: "THYAO, GARAN ASELS"
        candidates = [s for s in raw_symbols.replace(",", " ").split() if s.strip()]
        if not candidates:
            flash("Hisse kodu girilmedi.", "warning")
            return redirect(url_for("index"))

        # D-1: tek istekte sınırsız hisse eklenip dış kaynaklara istek
        # patlaması yaratılmasını engelle.
        if len(candidates) > MAX_SYMBOLS_PER_REQUEST:
            flash(
                f"Tek seferde en fazla {MAX_SYMBOLS_PER_REQUEST} hisse eklenebilir "
                f"({len(candidates)} kod girildi).",
                "warning",
            )
            return redirect(url_for("index"))

        added, skipped = [], []
        for raw in candidates:
            symbol = db.normalize_symbol(raw)

            # D-2, katman 1: biçim doğrulaması (ağ gerektirmez, her zaman uygulanır)
            ok_format, reason = db.validate_symbol_format(symbol)
            if not ok_format:
                skipped.append(f"{raw[:12]} ({reason})")
                continue

            # D-2, katman 2: canlı BIST listesine karşı doğrulama.
            # Ağ yoksa engellemez — yalnızca listede olmadığı kesinse reddeder.
            try:
                ok_exists, exists_reason = symbol_provider.is_valid_symbol(symbol)
                if not ok_exists:
                    skipped.append(f"{symbol} ({exists_reason})")
                    continue
            except Exception:
                pass  # doğrulama yapılamadıysa biçim kontrolüne güven

            # Şirket adını çözmeye çalış; ağ yoksa sessizce geç.
            try:
                name = symbol_provider.resolve_name(symbol)
            except Exception:
                name = None

            ok, msg = db.add_stock(
                symbol,
                name=name,
                # Toplu eklemede hedef fiyat tek hisseye anlamlı; birden fazlada atla.
                target_price=target if len(candidates) == 1 else None,
                alert_pct=alert,
                notes=notes if len(candidates) == 1 else None,
            )
            (added if ok else skipped).append(msg if not ok else symbol)

        if added:
            flash(f"Eklendi: {', '.join(added)}", "success")
        if skipped:
            flash(" · ".join(skipped), "warning")
        return redirect(url_for("index"))

    @app.route("/update/<symbol>", methods=["POST"])
    def update(symbol: str) -> Response:
        ok, msg = db.update_stock(
            symbol,
            target_price=_parse_optional_float(request.form.get("target_price")),
            alert_pct=_parse_optional_float(request.form.get("alert_pct")),
            notes=_clean_notes(request.form.get("notes")),
        )
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("index"))

    @app.route("/toggle/<symbol>", methods=["POST"])
    def toggle(symbol: str) -> Response:
        stock = db.get_stock(symbol)
        if not stock:
            flash(f"{symbol} bulunamadı.", "danger")
            return redirect(url_for("index"))
        ok, msg = db.set_active(symbol, not bool(stock["active"]))
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("index"))

    @app.route("/delete/<symbol>", methods=["POST"])
    def delete(symbol: str) -> Response:
        ok, msg = db.remove_stock(symbol)
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("index"))

    # ------------------------------ Çalıştırma ------------------------------

    @app.route("/run", methods=["POST"])
    def run() -> Response:
        mode = request.form.get("mode", "preview")
        # preview = e-posta gönderme, sadece HTML üret
        started, msg = jobs.start(dry_run=(mode == "preview"), force=True)
        flash(msg, "info" if started else "warning")
        return redirect(url_for("index"))

    @app.route("/status")
    def status() -> Response:
        """Arka plan işinin durumu (sayfa JS'i buradan sorgular)."""
        return jsonify(jobs.snapshot())

    @app.route("/preview")
    def preview() -> Response:
        """
        Son üretilen raporun HTML önizlemesi.

        Bu sayfa dış kaynaklardan (KAP, haber siteleri) gelen içerik barındırır.
        Jinja2 autoescape metin enjeksiyonunu, `sanitize_url` de `javascript:`
        gibi şemaları engeller; buradaki sıkı CSP üçüncü savunma katmanıdır —
        script'e hiç izin verilmez.
        """
        g.csp_override = security.CSP_PREVIEW

        if not jobs.last_html:
            return Response(
                "<p style='font-family:sans-serif;padding:24px'>"
                "Henüz bir rapor üretilmedi. Ana sayfadan “Önizleme oluştur” düğmesini kullan.</p>",
                mimetype="text/html; charset=utf-8",
            )
        return Response(jobs.last_html, mimetype="text/html; charset=utf-8")

    @app.route("/test-email", methods=["POST"])
    def test_email() -> Response:
        ok, msg = send_test_email()
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("index"))

    # ------------------------------ Hisse detayı ------------------------------

    @app.route("/hisse/<symbol>")
    def stock_detail(symbol: str) -> Any:
        """
        Tek bir hissenin detay sayfası: dönem grafiği, performans kutuları,
        KAP bildirimleri ve haberler.

        Fiyat ve grafik verisi sayfa açılırken sunucuda çekilmez — sayfa hemen
        açılır, veriler JavaScript ile arka planda yüklenir. Böylece yavaş bir
        kaynak tüm sayfayı bekletmez.
        """
        sym = db.normalize_symbol(symbol)
        ok_format, reason = db.validate_symbol_format(sym)
        if not ok_format:
            flash(f"Geçersiz hisse kodu: {reason}", "warning")
            return redirect(url_for("index"))

        stock = db.get_stock(sym)
        try:
            name = (stock or {}).get("name") or symbol_provider.resolve_name(sym)
        except Exception:
            name = (stock or {}).get("name")

        return render_template(
            "stock.html",
            symbol=sym,
            name=name,
            stock=stock,
            in_watchlist=stock is not None,
            periods=history_provider.PERIODS,
            default_period=history_provider.DEFAULT_PERIOD,
            config=config,
        )

    @app.route("/api/history/<symbol>")
    def api_history(symbol: str) -> Response:
        """Grafik verisi: seçilen dönemin OHLCV serisi ve özet istatistikleri."""
        sym = db.normalize_symbol(symbol)
        ok_format, reason = db.validate_symbol_format(sym)
        if not ok_format:
            return jsonify({"ok": False, "error": reason}), 400

        period = (request.args.get("period") or history_provider.DEFAULT_PERIOD).lower()
        if period not in history_provider.PERIODS:
            return jsonify({"ok": False, "error": "geçersiz dönem"}), 400

        result = history_provider.get_history(sym, period)
        if not result.ok or result.data is None:
            return jsonify({"ok": False, "error": "Fiyat geçmişi alınamadı."})

        return jsonify({"ok": True, **result.data.as_dict()})

    @app.route("/api/performance/<symbol>")
    def api_performance(symbol: str) -> Response:
        """Tüm dönemlerin yüzde değişimi (performans kutuları için)."""
        sym = db.normalize_symbol(symbol)
        ok_format, reason = db.validate_symbol_format(sym)
        if not ok_format:
            return jsonify({"ok": False, "error": reason}), 400

        try:
            performance = history_provider.get_performance(sym)
            # Sözlük yerine LİSTE döndürülüyor: Flask'ın jsonify'ı sözlük
            # anahtarlarını alfabetik sıralıyor ve dönemler 1a, 1g, 1h, 1y, 3a,
            # 6a diye karışıyordu. Liste sırayı korur.
            ordered = [
                {"key": key, **performance[key]}
                for key in history_provider.PERIODS
                if key in performance
            ]
            return jsonify({"ok": True, "periods": ordered})
        except Exception as exc:
            log.warning("%s performans hesabı başarısız: %s", sym, exc)
            return jsonify({"ok": False, "error": "Performans verisi alınamadı."})

    @app.route("/api/quotes")
    def api_quotes() -> Response:
        """
        Takip listesindeki tüm hisselerin güncel fiyatı — tek istekte.

        Liste sayfası açılırken her satır için ayrı istek atmak yerine hepsi
        burada toplanır. Hisseler paralel çekilir; biri düşerse yalnızca o
        satır "—" gösterir.
        """
        rows = db.list_stocks()
        symbols = [r["symbol"] for r in rows]
        if not symbols:
            return jsonify({"ok": True, "quotes": {}})

        quotes: dict[str, Any] = {}
        workers = max(1, min(config.max_workers, 8, len(symbols)))

        def fetch(sym: str):
            result = price_provider.get_price(sym)
            if not result.ok or result.data is None:
                return sym, None
            price = result.data
            return sym, {
                "last": price.last,
                "change": price.change,
                "change_percent": price.change_percent,
                "source": price.source,
            }

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch, s) for s in symbols]
            try:
                for future in as_completed(futures, timeout=config.run_timeout_seconds):
                    try:
                        sym, data = future.result()
                        quotes[sym] = data
                    except Exception:
                        continue
            except FuturesTimeout:
                log.warning("Kotasyon toplama süre sınırını aştı")

        return jsonify({"ok": True, "quotes": quotes})

    @app.route("/api/quote/<symbol>")
    def api_quote(symbol: str) -> Response:
        """Anlık fiyat kutusu için güncel kotasyon."""
        sym = db.normalize_symbol(symbol)
        ok_format, reason = db.validate_symbol_format(sym)
        if not ok_format:
            return jsonify({"ok": False, "error": reason}), 400

        result = price_provider.get_price(sym)
        if not result.ok or result.data is None:
            return jsonify({"ok": False, "error": "Fiyat alınamadı."})

        price = result.data
        return jsonify({
            "ok": True,
            "last": price.last,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "previous_close": price.previous_close,
            "change": price.change,
            "change_percent": price.change_percent,
            "volume": price.volume,
            "amount": price.amount,
            "source": price.source,
            "updated_at": price.updated_at.isoformat() if price.updated_at else None,
        })

    @app.route("/api/disclosures/<symbol>")
    def api_disclosures(symbol: str) -> Response:
        """Hisse detay sayfası için KAP bildirimleri ve haberler."""
        sym = db.normalize_symbol(symbol)
        ok_format, reason = db.validate_symbol_format(sym)
        if not ok_format:
            return jsonify({"ok": False, "error": reason}), 400

        try:
            name = symbol_provider.resolve_name(sym)
        except Exception:
            name = None

        def serialize(item) -> dict[str, Any]:
            return {
                "title": item.title,
                "url": item.url,
                "source": item.source,
                "summary": item.summary,
                "kind": item.kind,
                "published": item.published.isoformat() if item.published else None,
            }

        payload: dict[str, Any] = {"ok": True, "kap": [], "news": [], "errors": []}

        kap_result = kap_provider.get_disclosures(sym)
        if kap_result.ok:
            payload["kap"] = [serialize(i) for i in (kap_result.data or [])]
        else:
            payload["errors"].append("KAP bildirimleri alınamadı.")

        if config.news_enabled:
            news_result = news_provider.get_news(sym, company_name=name)
            if news_result.ok:
                payload["news"] = [serialize(i) for i in (news_result.data or [])]
            else:
                payload["errors"].append("Haberler alınamadı.")

        return jsonify(payload)

    # ------------------------------ Yardımcı API ------------------------------

    @app.route("/api/companies")
    def api_companies() -> Response:
        """
        Hisse seçici için arama. Kod ve şirket adında arar.

        Takip listesinde olanlar `added: true` ile işaretlenir; böylece seçici
        aynı hisseyi ikinci kez eklemeyi teklif etmez.
        """
        query = (request.args.get("q") or "")[:60]
        try:
            matches = symbol_provider.search_companies(query, limit=80)
        except Exception as exc:
            log.warning("Şirket araması başarısız: %s", exc)
            return jsonify({"ok": False, "error": "Şirket listesi alınamadı.", "companies": []})

        existing = {s["symbol"] for s in db.list_stocks()}
        return jsonify({
            "ok": True,
            "query": query,
            "companies": [
                {**c, "added": c["ticker"] in existing} for c in matches
            ],
        })

    @app.route("/api/bist100")
    def api_bist100() -> Response:
        """BIST 100 kodları (seçicide hızlı süzme için)."""
        try:
            return jsonify({"ok": True, "symbols": symbol_provider.bist100_symbols()})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "symbols": []})

    @app.route("/api/stocks")
    def api_stocks() -> Response:
        """Takip listesi (başka araçlarla entegrasyon için)."""
        return jsonify({"stocks": db.list_stocks()})

    # ------------------------------ Hata yönetimi ------------------------------
    # O-5: İstemciye ham istisna metni gösterilmez. Ayrıntı yalnızca log
    # dosyasına yazılır; kullanıcıya olayı log'da bulmasını sağlayacak
    # kısa bir referans kodu verilir.

    @app.errorhandler(Exception)
    def handle_unexpected(exc):  # pragma: no cover
        # HTTP hataları (403/404/400 vb.) kendi mesajlarıyla geçsin
        if isinstance(exc, HTTPException):
            return handle_http_error(exc)

        reference = secrets.token_hex(4)
        log.exception("Web arayüzünde beklenmeyen hata [ref=%s]", reference)
        return render_template("error.html", reference=reference, status=500), 500

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        # Güvenlik reddedilmeleri (400/403/401) kullanıcıya anlaşılır şekilde
        # gösterilir; iç ayrıntı sızdırmazlar.
        if exc.code in (400, 401, 403):
            log.warning("İstek reddedildi (%s): %s %s", exc.code, request.method, request.path)
        return (
            render_template("error.html", reference=None, status=exc.code, message=exc.description),
            exc.code or 500,
        )

    return app


def run_web() -> None:
    """
    Geliştirme sunucusunu başlatır.

    O-4: Werkzeug'un hata ayıklayıcısı tarayıcıdan rastgele Python kodu
    çalıştırmaya izin verir. Bu yüzden debug modu yalnızca sunucu loopback
    arayüzüne bağlıyken etkinleştirilir; aksi halde zorla kapatılır.
    """
    app = create_app()

    debug = config.flask_debug
    if debug and not config.is_bound_to_localhost:
        log.error(
            "FLASK_DEBUG=true ama FLASK_HOST=%s (localhost değil). "
            "Werkzeug hata ayıklayıcısı uzaktan kod çalıştırmaya izin verdiği için "
            "debug modu zorla kapatıldı.",
            config.flask_host,
        )
        debug = False

    scheme = "https" if config.session_cookie_secure else "http"
    log.info("Web arayüzü: %s://%s:%s", scheme, config.flask_host, config.flask_port)

    if security.auth_enabled():
        log.info("Kimlik doğrulama etkin — arayüze erişim için kimlik bilgisi gerekiyor.")
    elif not config.is_bound_to_localhost:
        # create_app zaten reddeder; buraya düşmemeli.
        log.error("Kimlik doğrulama yok ve arayüz dışarı açık — bu yapılandırma desteklenmiyor.")

    app.run(
        host=config.flask_host,
        port=config.flask_port,
        debug=debug,
        use_reloader=False,  # reloader zamanlayıcıyı ikiye katlar
    )
