"""
Web arayüzü güvenlik katmanı.

Bu modül dört savunmayı sağlar:

1. **CSRF koruması** — Oturuma bağlı token; tüm durum değiştiren POST
   isteklerinde zorunlu. Flask CSRF korumasını yerleşik olarak sunmaz.
2. **Host başlığı doğrulaması** — DNS rebinding saldırılarına karşı.
   127.0.0.1'e bağlanmak uzaktan doğrudan erişimi engeller ama rebinding'i
   engellemez; tek etkili savunma Host başlığını doğrulamaktır.
3. **Kimlik doğrulama** — Opsiyonel token veya HTTP Basic. Uygulama
   localhost dışına açılmışsa zorunlu hale gelir.
4. **Güvenlik başlıkları** — CSP, X-Frame-Options, X-Content-Type-Options,
   Referrer-Policy.

Tüm gizli karşılaştırmalar `hmac.compare_digest` ile sabit zamanlıdır;
karakter karakter erken çıkan `==` karşılaştırması zamanlama sızıntısı yaratır.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import ipaddress
import secrets
from functools import wraps
from typing import Callable
from urllib.parse import urlsplit

from flask import Response, abort, g, request, session

from config import config
from logging_setup import get_logger

log = get_logger(__name__)

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Durum değiştirmeyen, korumaya gerek olmayan HTTP metotları
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Yerel kabul edilen host adları
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


# ---------------------------------------------------------------------------
# 1. CSRF
# ---------------------------------------------------------------------------

def get_csrf_token() -> str:
    """
    Oturumdaki CSRF token'ını döndürür; yoksa üretir.

    Token oturum çerezinde saklanır. Çerez `SameSite=Strict` ve `HttpOnly`
    olduğu için başka bir site tarafından okunamaz ve çoğu tarayıcıda
    cross-site POST ile gönderilmez — token bunun üzerine ikinci katmandır.
    """
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf() -> None:
    """
    İsteğin CSRF token'ını doğrular. Geçersizse 403 ile isteği sonlandırır.

    İki katmanlı kontrol:
      a) Origin/Referer başlığı beklenen host ile eşleşmeli (tarayıcı bunları
         sahteleyemez — kötü niyetli JavaScript bu başlıkları değiştiremez).
      b) Form/başlık token'ı oturumdaki token ile eşleşmeli.
    """
    # --- (a) Origin / Referer kontrolü ---
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin:
        origin_host = urlsplit(origin).netloc
        if origin_host and not _hosts_match(origin_host, request.host):
            log.warning(
                "CSRF: Origin/Referer uyuşmuyor (gelen=%r, beklenen=%r)",
                origin_host, request.host,
            )
            abort(403, description="Cross-site istek reddedildi.")

    # --- (b) Token kontrolü ---
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.form.get(CSRF_FORM_FIELD) or request.headers.get(CSRF_HEADER, "")

    if not expected or not submitted or not hmac.compare_digest(str(expected), str(submitted)):
        log.warning("CSRF: token doğrulanamadı (%s %s)", request.method, request.path)
        abort(403, description="CSRF token geçersiz veya eksik. Sayfayı yenileyip tekrar dene.")


def _hosts_match(a: str, b: str) -> bool:
    """İki host:port değerini port farkını yok sayarak karşılaştırır."""
    return a.split(":")[0].strip("[]").lower() == b.split(":")[0].strip("[]").lower()


# ---------------------------------------------------------------------------
# 2. Host başlığı doğrulaması (DNS rebinding savunması)
# ---------------------------------------------------------------------------

def is_local_host_value(host: str) -> bool:
    """Verilen Host başlığı yerel bir adrese mi işaret ediyor?"""
    hostname = host.split(":")[0].strip("[]").lower()
    if hostname in LOCAL_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def allowed_hosts() -> set[str]:
    """
    İzin verilen Host değerleri.

    Varsayılan olarak yalnızca loopback adresleri. Uygulamayı bir ağ adı
    üzerinden kullanacaksan `.env`'de ALLOWED_HOSTS ile ekle.
    """
    hosts = {"localhost", "127.0.0.1", "::1"}
    hosts.update(h.lower() for h in config.allowed_hosts)
    # Bağlanılan adres localhost değilse onu da otomatik ekle
    if config.flask_host not in ("0.0.0.0", "::"):
        hosts.add(config.flask_host.lower())
    return hosts


def validate_host() -> None:
    """
    Host başlığını izinli listeye karşı doğrular.

    DNS rebinding saldırısında saldırganın alan adı 127.0.0.1'e çözümlenir ve
    tarayıcı uygulamayı aynı kaynak sayar. İstek bu durumda uygulamaya
    saldırganın alan adıyla ulaşır — bu kontrol tam olarak orada devreye girer.
    """
    hostname = request.host.split(":")[0].strip("[]").lower()

    if is_local_host_value(request.host):
        return
    if hostname in allowed_hosts():
        return

    log.warning("Reddedilen Host başlığı: %r (DNS rebinding olabilir)", request.host)
    abort(400, description="Geçersiz Host başlığı.")


# ---------------------------------------------------------------------------
# 3. Kimlik doğrulama (opsiyonel)
# ---------------------------------------------------------------------------

def auth_enabled() -> bool:
    """`.env`'de kimlik doğrulama tanımlanmış mı?"""
    return bool(config.auth_token or (config.auth_user and config.auth_password))


def _check_bearer_or_token(value: str) -> bool:
    """Token'ı sabit zamanlı karşılaştırır."""
    if not config.auth_token:
        return False
    return hmac.compare_digest(value.strip(), config.auth_token)


def _check_basic(header_value: str) -> bool:
    """HTTP Basic kimlik bilgisini sabit zamanlı doğrular."""
    if not (config.auth_user and config.auth_password):
        return False
    try:
        decoded = base64.b64decode(header_value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    user, _, password = decoded.partition(":")
    # Her iki alan da her zaman karşılaştırılır — erken çıkış yok.
    user_ok = hmac.compare_digest(user, config.auth_user)
    pass_ok = hmac.compare_digest(password, config.auth_password)
    return user_ok and pass_ok


def check_auth() -> None:
    """
    Kimlik doğrulamayı uygular.

    Kimlik doğrulama tanımlı değilse ve uygulama yalnızca localhost'a
    bağlıysa geçilir (kişisel kullanım senaryosu). Localhost dışına
    bağlanmışsa `create_app` zaten başlatmayı reddeder.
    """
    if not auth_enabled():
        return

    # Oturumda daha önce doğrulanmışsa tekrar sorma
    if session.get("_authenticated"):
        return

    header = request.headers.get("Authorization", "")

    if header.startswith("Bearer ") and _check_bearer_or_token(header[7:]):
        session["_authenticated"] = True
        return
    if header.startswith("Basic ") and _check_basic(header[6:]):
        session["_authenticated"] = True
        return
    # Token'ı sorgu parametresi olarak da kabul et (ilk giriş kolaylığı)
    token_param = request.args.get("token", "")
    if token_param and _check_bearer_or_token(token_param):
        session["_authenticated"] = True
        return

    log.warning("Yetkisiz erişim denemesi: %s %s", request.method, request.path)
    response = Response(
        "Kimlik doğrulama gerekli.\n", status=401, mimetype="text/plain; charset=utf-8"
    )
    response.headers["WWW-Authenticate"] = 'Basic realm="BIST Takip", charset="UTF-8"'
    abort(response)


# ---------------------------------------------------------------------------
# 4. Güvenlik başlıkları
# ---------------------------------------------------------------------------

# Arayüz tüm CSS/JS'i yerel `static/` klasöründen yükler (Bootstrap paketlenmiş),
# bu yüzden dış kaynağa hiç izin vermemize gerek yok.
CSP_APP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "   # index.html içindeki küçük satır içi script
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)

# Önizleme sayfası dış kaynaklı içerik (haber başlıkları/linkleri) barındırır.
# Orada script'e hiç izin verilmez — O-3 bulgusuna karşı ikinci savunma katmanı.
CSP_PREVIEW = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def apply_security_headers(response: Response) -> Response:
    """Her yanıta güvenlik başlıklarını ekler."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # Tarayıcının bu sayfayı önbelleğe alıp diske yazmasını istemiyoruz
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault(
        "Content-Security-Policy", getattr(g, "csp_override", None) or CSP_APP
    )
    return response


# ---------------------------------------------------------------------------
# URL şeması doğrulaması (O-3)
# ---------------------------------------------------------------------------

ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def is_safe_url(url: str | None) -> bool:
    """
    Dış kaynaktan gelen bir URL'in bağlantı olarak gömülmesi güvenli mi?

    Yalnızca http/https kabul edilir. `javascript:`, `data:`, `vbscript:` ve
    `file:` gibi şemalar reddedilir — bunlar tıklandığında sayfanın kendi
    kaynağında kod çalıştırabilir.
    """
    if not url or not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate:
        return False
    # Kontrol karakterleri şema kontrolünü atlatmak için kullanılabilir
    # (örn. "java\tscript:") — böyle bir URL'e hiç güvenmiyoruz.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return False
    try:
        scheme = urlsplit(candidate).scheme.lower()
    except ValueError:
        return False
    return scheme in ALLOWED_URL_SCHEMES


def sanitize_url(url: str | None) -> str:
    """Güvenliyse URL'i döndürür, değilse boş string."""
    return url.strip() if is_safe_url(url) else ""


# ---------------------------------------------------------------------------
# Flask'a bağlama
# ---------------------------------------------------------------------------

def init_app(app) -> None:
    """
    Güvenlik katmanını Flask uygulamasına bağlar.

    Sıra önemli: önce Host doğrulaması (en ucuz ve en temel kontrol),
    sonra kimlik doğrulama, en son CSRF.
    """
    # Oturum çerezi sertleştirme
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,      # JavaScript çerezi okuyamaz
        SESSION_COOKIE_SAMESITE="Strict",  # cross-site isteklerde çerez gönderilmez
        SESSION_COOKIE_NAME="bist_takip_session",
        # HTTPS ardında çalıştırılıyorsa .env'den açılabilir
        SESSION_COOKIE_SECURE=config.session_cookie_secure,
        MAX_CONTENT_LENGTH=256 * 1024,     # form gövdesi üst sınırı (DoS koruması)
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    )

    @app.before_request
    def _security_gate():
        validate_host()
        check_auth()
        if request.method not in SAFE_METHODS:
            validate_csrf()

    @app.after_request
    def _headers(response):
        return apply_security_headers(response)

    # Şablonlarda {{ csrf_token() }} olarak kullanılabilsin
    app.jinja_env.globals["csrf_token"] = get_csrf_token

    log.info(
        "Güvenlik katmanı etkin — Host doğrulaması: açık, CSRF: açık, kimlik doğrulama: %s",
        "açık" if auth_enabled() else "kapalı (yalnızca localhost)",
    )


def csrf_exempt_field() -> str:
    """Şablonlarda kullanılacak gizli input HTML'i (yedek kullanım)."""
    return f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{get_csrf_token()}">'
