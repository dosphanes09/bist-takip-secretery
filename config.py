"""
Uygulama konfigürasyonu.

Tüm ayarlar `.env` dosyasından okunur. Hiçbir gizli bilgi (SMTP şifresi vb.)
kod içine gömülmez. `.env.example` dosyasını `.env` olarak kopyalayıp
kendi bilgilerinle doldur.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Proje kök dizini — DB ve log dosyaları buraya göre konumlanır.
BASE_DIR = Path(__file__).resolve().parent

# .env dosyasını yükle (varsa). Zaten tanımlı ortam değişkenlerini ezmez.
load_dotenv(BASE_DIR / ".env")


def _get_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "evet", "on"}


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip().replace(",", "."))
    except ValueError:
        return default


def _get_list(key: str, default: list[str] | None = None) -> list[str]:
    """Virgülle ayrılmış değerleri listeye çevirir."""
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return list(default or [])
    return [p.strip() for p in raw.split(",") if p.strip()]


# E-posta adresi biçim kontrolü (D-3).
# Amaç mükemmel RFC 5322 uyumu değil; satır sonu/kontrol karakteri içeren
# değerlerin başlık alanına ulaşmasını en baştan engellemek.
_EMAIL_RE = re.compile(r"^[^\s@<>,;:\\\"]+@[^\s@<>,;:\\\"]+\.[A-Za-z]{2,}$")


def is_valid_email(address: str) -> bool:
    """Adres biçim olarak geçerli ve kontrol karakteri içermiyor mu?"""
    if not address or len(address) > 254:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in address):
        return False
    return bool(_EMAIL_RE.match(address.strip()))


def _harden_permissions(path: Path, is_dir: bool = False) -> None:
    """
    Dosya/dizin izinlerini yalnızca sahibine açık hale getirir (O-6).

    Linux/macOS'ta POSIX kipleri (0600 / 0700) kullanılır.
    Windows'ta NTFS erişim denetim listeleri (ACL) `icacls` ile ayarlanır —
    POSIX kipleri Windows'ta hiçbir şey ifade etmez, dolayısıyla `chmod`
    çağırmak sessizce etkisiz kalırdı.
    """
    if not path.exists():
        return

    if os.name == "posix":
        try:
            target = stat.S_IRWXU if is_dir else (stat.S_IRUSR | stat.S_IWUSR)
            current = stat.S_IMODE(path.stat().st_mode)
            if current & (stat.S_IRWXG | stat.S_IRWXO):
                path.chmod(target)
        except OSError:
            # İzin değiştirilemiyorsa (ör. ağ sürücüsü) uygulamayı durdurma.
            pass
        return

    if os.name == "nt":
        _harden_permissions_windows(path, is_dir)


def _harden_permissions_windows(path: Path, is_dir: bool = False) -> None:
    """
    Windows'ta NTFS izinlerini yalnızca mevcut kullanıcıya daraltır.

    Ne yapıyor:
      /inheritance:r  → üst klasörden devralınan izinleri kaldırır
      /grant:r USER   → yalnızca bu kullanıcıya tam yetki verir (öncekileri değiştirir)

    Sonuç: makinedeki diğer kullanıcılar (ve devralınmış geniş gruplar) dosyayı
    okuyamaz. Yöneticiler ve SYSTEM yine erişebilir — bu Windows'ta kaçınılmaz,
    yönetici zaten her dosyanın sahipliğini alabilir.

    Hata durumunda sessizce geçer: izin sıkılaştıramamak uygulamayı
    durdurmayı gerektirecek kadar kritik değildir, ama `verify_permissions()`
    ile durum raporlanabilir.
    """
    import subprocess

    user = os.environ.get("USERNAME")
    if not user:
        return
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{user}" if domain else user

    # Klasörlerde izinler alt öğelere de uygulanmalı: (OI) nesne devralma,
    # (CI) kapsayıcı devralma, F tam yetki.
    grant = f"{principal}:(OI)(CI)F" if is_dir else f"{principal}:F"

    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
            timeout=20,
            check=False,
            # Konsol penceresi açılmasın (zamanlayıcıdan çalışırken rahatsız eder)
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def is_permission_too_open(path: Path) -> bool:
    """
    Dosya, sahibinden başkaları tarafından da okunabiliyor mu?

    POSIX'te grup/diğer bitlerine bakılır. Windows'ta ACL girdilerinde geniş
    kapsamlı gruplar (Users, Everyone, Authenticated Users) aranır —
    Administrators ve SYSTEM hariç tutulur, çünkü onlar Windows'ta her
    dosyaya zaten erişebilir ve kaldırılmaları anlamlı bir koruma sağlamaz.
    """
    if not path.exists():
        return False

    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))

    if os.name == "nt":
        acl = describe_permissions(path).lower()
        broad = ("everyone", "herkes", "\\users", "\\kullanıcılar",
                 "authenticated users", "kimliği doğrulanmış")
        return any(term in acl for term in broad)

    return False


def describe_permissions(path: Path) -> str:
    """
    Bir dosyanın erişim izinlerini insan okunabilir biçimde döndürür.
    Denetim/raporlama için — hiçbir şeyi değiştirmez.
    """
    if not path.exists():
        return "dosya yok"

    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        others = "başkaları okuyabilir" if mode & (stat.S_IRWXG | stat.S_IRWXO) else "yalnızca sahibi"
        return f"{oct(mode)[-3:]} — {others}"

    if os.name == "nt":
        import subprocess

        try:
            result = subprocess.run(
                ["icacls", str(path)],
                capture_output=True, text=True, timeout=20, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            # İlk satır dosya yolunu içerir; erişim girdileri onu izler.
            entries = []
            for line in lines:
                if line.lower().startswith("successfully") or line.lower().startswith("başarıyla"):
                    continue
                # "C:\yol\dosya KULLANICI:(F)" → yalnızca izin kısmını al
                part = line.split(str(path))[-1].strip() if str(path) in line else line
                if part:
                    entries.append(part)
            return " | ".join(entries) if entries else "okunamadı"
        except (OSError, subprocess.SubprocessError):
            return "okunamadı"

    return "bilinmeyen platform"


@dataclass
class SmtpConfig:
    """E-posta gönderimi için SMTP ayarları."""

    host: str = field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    port: int = field(default_factory=lambda: _get_int("SMTP_PORT", 587))
    user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    # 587 -> STARTTLS, 465 -> doğrudan SSL
    use_ssl: bool = field(default_factory=lambda: _get_bool("SMTP_USE_SSL", False))
    use_tls: bool = field(default_factory=lambda: _get_bool("SMTP_USE_TLS", True))
    timeout: int = field(default_factory=lambda: _get_int("SMTP_TIMEOUT", 30))

    mail_from: str = field(default_factory=lambda: os.getenv("MAIL_FROM", ""))
    mail_from_name: str = field(
        default_factory=lambda: os.getenv("MAIL_FROM_NAME", "BIST Takip")
    )
    mail_to: list[str] = field(default_factory=lambda: _get_list("MAIL_TO"))

    # Biçim kontrolünden geçemeyen adresler — başlatma sırasında uyarılır.
    invalid_recipients: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # MAIL_FROM boşsa SMTP_USER'a düş (Gmail'de ikisi genelde aynıdır).
        if not self.mail_from:
            self.mail_from = self.user

        # --- E-posta adresi doğrulaması (D-3) ---
        # Satır sonu içeren bir adres Python tarafından zaten reddedilir, ama
        # hata mesajı anlaşılmaz olur. Burada erkenden ve net şekilde eleriz.
        valid, invalid = [], []
        for address in self.mail_to:
            (valid if is_valid_email(address) else invalid).append(address)
        self.mail_to = valid
        self.invalid_recipients = invalid

        if self.mail_from and not is_valid_email(self.mail_from):
            self.invalid_recipients.append(f"MAIL_FROM={self.mail_from}")
            self.mail_from = ""

        # Görünen ad da başlığa girdiği için kontrol karakterlerinden arındırılır.
        self.mail_from_name = "".join(
            ch for ch in self.mail_from_name if ord(ch) >= 0x20 and ord(ch) != 0x7F
        )[:78]

    # `.env.example` ile gelen şablon değerler. Bunlar boş olmadıkları için
    # "dolu" sayılırlardı ve uygulama sahte kimlik bilgileriyle göndermeye
    # kalkıp anlamsız bir SMTP hatası veriyordu (canlı testte yakalandı).
    _PLACEHOLDERS = frozenset({
        "ornek@gmail.com",
        "xxxx xxxx xxxx xxxx",
        "senin.adresin@gmail.com",
        "ornek@ornek.com",
        "example@gmail.com",
        "changeme",
    })

    @classmethod
    def _is_placeholder(cls, value: str) -> bool:
        """Değer `.env.example` şablonundan hiç değiştirilmemiş mi?"""
        candidate = (value or "").strip().lower()
        if not candidate:
            return True
        if candidate in cls._PLACEHOLDERS:
            return True
        # "xxxx xxxx..." gibi yalnızca x ve boşluktan oluşan şifreler
        if candidate.replace(" ", "") and set(candidate.replace(" ", "")) == {"x"}:
            return True
        return False

    @property
    def is_configured(self) -> bool:
        """
        E-posta göndermek için gerçek (şablon olmayan) bilgi var mı?

        Sadece "boş değil" kontrolü yetmez — `.env.example` her alanı örnek
        bir değerle doldurulmuş hâlde gönderir.
        """
        return not self.missing_fields()

    def missing_fields(self) -> list[str]:
        """
        Eksik ya da hâlâ şablon değerinde olan zorunlu alanların listesi.
        Hata mesajlarında ve arayüzde gösterilir.
        """
        missing = []
        if not self.host:
            missing.append("SMTP_HOST")
        if self._is_placeholder(self.user):
            missing.append("SMTP_USER")
        if self._is_placeholder(self.password):
            missing.append("SMTP_PASSWORD")
        if not self.mail_to or all(self._is_placeholder(a) for a in self.mail_to):
            missing.append("MAIL_TO")
        if self._is_placeholder(self.mail_from):
            missing.append("MAIL_FROM")
        return missing


@dataclass
class Config:
    """Uygulamanın tüm ayarlarını tek noktada toplar."""

    # --- Veritabanı ---
    db_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("DB_PATH", str(BASE_DIR / "data" / "bist.db"))
        )
    )

    # --- Zaman / zamanlama ---
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Europe/Istanbul"))
    # Piyasa kapanışı sonrası günlük rapor saati (HH:MM, yerel saat)
    daily_send_time: str = field(default_factory=lambda: os.getenv("DAILY_SEND_TIME", "18:30"))
    # Gün içi ek kontroller (opsiyonel). Örn: "11:00,15:00"
    intraday_enabled: bool = field(default_factory=lambda: _get_bool("INTRADAY_ENABLED", False))
    intraday_times: list[str] = field(default_factory=lambda: _get_list("INTRADAY_TIMES", []))
    # Hafta sonu rapor gönderilmesin mi?
    skip_weekends: bool = field(default_factory=lambda: _get_bool("SKIP_WEEKENDS", True))

    # --- Veri kaynakları ---
    # Fiyat sağlayıcı önceliği. Sırayla denenir, ilk başarılı olan kullanılır.
    price_providers: list[str] = field(
        default_factory=lambda: _get_list("PRICE_PROVIDERS", ["borsapy", "yfinance"])
    )
    # KAP bildirimlerinde kaç gün geriye bakılsın
    kap_lookback_days: int = field(default_factory=lambda: _get_int("KAP_LOOKBACK_DAYS", 1))
    kap_max_items: int = field(default_factory=lambda: _get_int("KAP_MAX_ITEMS", 8))
    # Google News RSS
    news_enabled: bool = field(default_factory=lambda: _get_bool("NEWS_ENABLED", True))
    news_lookback_hours: int = field(default_factory=lambda: _get_int("NEWS_LOOKBACK_HOURS", 36))
    news_max_items: int = field(default_factory=lambda: _get_int("NEWS_MAX_ITEMS", 5))
    # Analist hedef fiyatları (bulunamazsa süreç bloklanmaz)
    analysts_enabled: bool = field(default_factory=lambda: _get_bool("ANALYSTS_ENABLED", True))

    # Ağ ayarları
    request_timeout: int = field(default_factory=lambda: _get_int("REQUEST_TIMEOUT", 20))
    # Hisseler paralel çekilsin mi (hızlı) yoksa sırayla mı (nazik).
    # report.py ayrıca MAX_ALLOWED_WORKERS ile üst sınır uygular.
    max_workers: int = field(default_factory=lambda: _get_int("MAX_WORKERS", 4))
    # Tüm rapor çalıştırması için üst süre sınırı (saniye). Asılı kalan bir
    # veri kaynağının zamanlayıcıyı süresiz dondurmasını engeller (O-2).
    run_timeout_seconds: int = field(
        default_factory=lambda: _get_int("RUN_TIMEOUT_SECONDS", 600)
    )

    # --- Alarm varsayılanları ---
    # Hisse bazında ayrı eşik girilmemişse bu yüzde kullanılır.
    default_alert_pct: float = field(default_factory=lambda: _get_float("DEFAULT_ALERT_PCT", 5.0))

    # --- Web arayüzü ---
    flask_host: str = field(default_factory=lambda: os.getenv("FLASK_HOST", "127.0.0.1"))
    flask_port: int = field(default_factory=lambda: _get_int("FLASK_PORT", 5000))
    flask_debug: bool = field(default_factory=lambda: _get_bool("FLASK_DEBUG", False))

    # Secret key artık sabit bir varsayılana düşmez (O-1).
    # Tanımlı değilse `data/secret_key` dosyasından okunur, o da yoksa üretilir.
    secret_key: str = field(default_factory=lambda: os.getenv("FLASK_SECRET_KEY", ""))

    # --- Güvenlik ---
    # Host başlığı izinli listesi (DNS rebinding savunması, K-2).
    allowed_hosts: list[str] = field(default_factory=lambda: _get_list("ALLOWED_HOSTS", []))
    # Opsiyonel kimlik doğrulama. Localhost dışına açılırsa zorunlu hale gelir.
    auth_token: str = field(default_factory=lambda: os.getenv("AUTH_TOKEN", "").strip())
    auth_user: str = field(default_factory=lambda: os.getenv("AUTH_USER", "").strip())
    auth_password: str = field(default_factory=lambda: os.getenv("AUTH_PASSWORD", "").strip())
    # HTTPS ardında (ters proxy) çalıştırılıyorsa true yap.
    session_cookie_secure: bool = field(
        default_factory=lambda: _get_bool("SESSION_COOKIE_SECURE", False)
    )

    # --- Loglama ---
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_path: Path = field(
        default_factory=lambda: Path(os.getenv("LOG_PATH", str(BASE_DIR / "logs" / "bist.log")))
    )

    # --- SMTP ---
    smtp: SmtpConfig = field(default_factory=SmtpConfig)

    def ensure_dirs(self) -> None:
        """
        DB ve log dizinlerini oluşturur ve izinlerini sıkılaştırır (O-6).

        Bu dizinler takip listesini ve hata kayıtlarını barındırır; çok
        kullanıcılı makinelerde başka kullanıcılara açık olmamalıdır.
        """
        for directory in (self.db_path.parent, self.log_path.parent):
            directory.mkdir(parents=True, exist_ok=True)
            _harden_permissions(directory, is_dir=True)

    # ---------------------------------------------------------------
    # Secret key yönetimi (O-1)
    # ---------------------------------------------------------------

    # Eski sürümlerde ya da örnek dosyada geçmiş, kullanılmaması gereken
    # anahtarlar. Bu listedeki bir değer `.env`'de bulunursa yok sayılır.
    _KNOWN_WEAK_SECRETS = frozenset({
        "bist-takip-dev-key",
        "bunu-rastgele-bir-degerle-degistir",
        "changeme", "secret", "dev", "test", "password",
    })

    _weak_secret_detected: bool = False

    @classmethod
    def _is_weak_secret(cls, value: str) -> bool:
        """Anahtar tahmin edilebilir mi ya da yetersiz uzunlukta mı?"""
        candidate = value.strip()
        if candidate.lower() in cls._KNOWN_WEAK_SECRETS:
            return True
        # 32 karakterin altındaki anahtarlar kaba kuvvete karşı yetersiz.
        if len(candidate) < 32:
            return True
        # Tek karakterden oluşan ya da çok az çeşitlilik içeren değerler
        if len(set(candidate)) < 8:
            return True
        return False

    def resolve_secret_key(self) -> str:
        """
        Flask secret key'ini çözer.

        Öncelik:
          1. `.env`'deki FLASK_SECRET_KEY (yeterince güçlüyse)
          2. `data/secret_key` dosyası (daha önce üretilmiş)
          3. Yeni üret ve 0600 izinle kaydet

        Sabit bir varsayılan **bilinçli olarak yoktur**: kaynak kodda ya da
        örnek dosyada görünen bir anahtar, oturum çerezlerinin taklit
        edilmesine izin verir. Eski sürümden kalma zayıf/şablon anahtarlar
        da reddedilir.
        """
        if self.secret_key and not self._is_weak_secret(self.secret_key):
            return self.secret_key

        if self.secret_key:
            # Zayıf anahtar tespit edildi — yok say ve güçlüsünü üret.
            self._weak_secret_detected = True
            self.secret_key = ""

        key_file = self.db_path.parent / "secret_key"

        if key_file.exists():
            try:
                stored = key_file.read_text(encoding="utf-8").strip()
                if stored:
                    _harden_permissions(key_file)
                    self.secret_key = stored
                    return stored
            except OSError:
                pass

        generated = secrets.token_urlsafe(48)
        try:
            self.ensure_dirs()
            key_file.write_text(generated, encoding="utf-8")
            _harden_permissions(key_file)
        except OSError:
            # Yazılamazsa da devam et — anahtar bu süreç boyunca geçerli olur
            # (yeniden başlatınca oturumlar düşer, ama güvenlik bozulmaz).
            pass

        self.secret_key = generated
        return generated

    # ---------------------------------------------------------------
    # Başlangıç güvenlik kontrolleri
    # ---------------------------------------------------------------

    @property
    def is_bound_to_localhost(self) -> bool:
        """Web sunucusu yalnızca yerel arayüze mi bağlanıyor?"""
        return self.flask_host in ("127.0.0.1", "localhost", "::1")

    def check_env_file_permissions(self) -> str | None:
        """
        `.env` dosyası başkalarınca okunabiliyorsa uyarı metni döndürür (O-6).
        İzni düzeltmeyi de dener.
        """
        env_file = BASE_DIR / ".env"
        if os.name != "posix" or not env_file.exists():
            return None
        mode = stat.S_IMODE(env_file.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _harden_permissions(env_file)
            new_mode = stat.S_IMODE(env_file.stat().st_mode)
            if new_mode & (stat.S_IRWXG | stat.S_IRWXO):
                return (
                    f".env dosyası başka kullanıcılar tarafından okunabilir "
                    f"(izin {oct(mode)[-3:]}). SMTP şifreni içeriyor — "
                    f"`chmod 600 .env` çalıştır."
                )
            return f".env izinleri {oct(mode)[-3:]} → 600 olarak düzeltildi."
        return None

    def security_warnings(self) -> list[str]:
        """Başlangıçta kullanıcıya gösterilecek güvenlik uyarıları."""
        warnings: list[str] = []

        env_warning = self.check_env_file_permissions()
        if env_warning:
            warnings.append(env_warning)

        if self._weak_secret_detected:
            warnings.append(
                "FLASK_SECRET_KEY zayıf ya da şablon değeri olduğu için yok sayıldı; "
                "yerine güçlü bir anahtar üretildi (data/secret_key). "
                ".env'deki satırı boş bırakabilirsin."
            )

        if self.invalid_smtp_recipients:
            warnings.append(
                "Geçersiz e-posta adresi yok sayıldı: "
                + ", ".join(self.invalid_smtp_recipients)
            )

        if not self.is_bound_to_localhost:
            warnings.append(
                f"FLASK_HOST={self.flask_host} — arayüz yerel makine dışına açık. "
                "Kimlik doğrulama ve HTTPS olmadan kullanma."
            )

        if self.flask_debug and not self.is_bound_to_localhost:
            warnings.append(
                "FLASK_DEBUG localhost dışı bağlantıda zorla kapatıldı "
                "(Werkzeug hata ayıklayıcısı uzaktan kod çalıştırmaya izin verir)."
            )

        return warnings

    @property
    def invalid_smtp_recipients(self) -> list[str]:
        return self.smtp.invalid_recipients

    def fatal_security_errors(self) -> list[str]:
        """
        Uygulamanın başlatılmasını engelleyecek kadar ciddi yapılandırma
        hataları. Boş liste = başlatılabilir.
        """
        errors: list[str] = []

        # K-2: Dışarıya açık + kimlik doğrulama yok = kabul edilemez.
        if not self.is_bound_to_localhost:
            has_auth = bool(self.auth_token or (self.auth_user and self.auth_password))
            if not has_auth:
                errors.append(
                    f"FLASK_HOST={self.flask_host} olarak ayarlanmış ama kimlik doğrulama "
                    "tanımlı değil. .env dosyasında AUTH_TOKEN ya da "
                    "AUTH_USER + AUTH_PASSWORD tanımla, veya FLASK_HOST=127.0.0.1 yap."
                )
            if self.auth_password and len(self.auth_password) < 12:
                errors.append(
                    "AUTH_PASSWORD çok kısa (en az 12 karakter olmalı)."
                )

        return errors


# Uygulama genelinde paylaşılan tek konfigürasyon nesnesi.
config = Config()
