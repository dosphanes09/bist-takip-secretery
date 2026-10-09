# BIST Hisse Takip ve Bilgilendirme Uygulaması

Takip listendeki BIST hisseleri için her gün **fiyat bilgisi, KAP bildirimleri,
haberler ve analist hedef fiyatlarını** derleyip HTML e-posta olarak gönderen
yerel bir uygulama. Takip listesi basit bir web arayüzünden yönetilir.

```
┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────┐
│ Web arayüzü │──▶│   SQLite     │──▶│  Sağlayıcı  │──▶│  E-posta │
│ (Flask)     │   │  takip list. │   │  zinciri    │   │  (SMTP)  │
└─────────────┘   └──────────────┘   └─────────────┘   └──────────┘
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    ▼                       ▼                       ▼
            borsapy → yfinance      KAP API → borsapy        Google News RSS
              (fiyat)                (bildirimler)              (haberler)
```

---

## 0. Windows'ta Tek Tıkla Kullanım (en kolay yol)

Terminalle uğraşmak istemiyorsan bu bölüm yeterli.

1. Zip'i aç (örn. `C:\Users\<adın>\bist_takip`).
   **Program Files ya da System32 gibi korumalı klasörlere açma** — yazma izni gerekiyor.
2. Klasördeki **`Masaustu-Kisayol-Olustur.bat`** dosyasına çift tıkla.
   Masaüstünde iki kısayol oluşur:
   - **BIST Takip** → arayüzü açar + günlük zamanlayıcıyı çalıştırır
   - **BIST Takip - Raporu Gonder** → raporu bir kez üretip e-posta gönderir
3. **BIST Takip** kısayoluna çift tıkla.

İlk çalıştırmada başlatıcı kurulumu kendisi yapar: Python'u bulur, sanal ortamı
kurar, paketleri indirir (birkaç dakika), `.env` dosyasını oluşturup Not
Defteri'nde açar. Sonraki açılışlarda doğrudan arayüz açılır (birkaç saniye).

`.env` dosyasında **SMTP_USER**, **SMTP_PASSWORD** ve **MAIL_TO** satırlarını
doldurup kaydet (Gmail için Uygulama Şifresi — bkz. bölüm 3). Doldurmadan da
arayüzü kullanıp "Önizleme oluştur" ile raporu görebilirsin; yalnızca e-posta
gönderimi çalışmaz.

**Uygulamayı durdurmak:** açılan siyah pencereyi kapat. Pencere açık kaldığı
sürece günlük rapor saatinde otomatik gönderim yapılır.

> Windows "Bu uygulama korundu" (SmartScreen) uyarısı verirse **Ek bilgi →
> Yine de çalıştır** de. Bu uyarı imzasız her `.bat` dosyası için çıkar.

---

## 1. Hızlı Başlangıç

```bash
# 1) Bağımlılıkları kur (sanal ortam önerilir)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) Konfigürasyonu hazırla
cp .env.example .env               # Windows: copy .env.example .env
# .env dosyasını açıp SMTP bilgilerini doldur (bkz. bölüm 3)

# 3) Kurulumun sağlam olduğunu doğrula (ağ gerektirmez)
python app.py --self-test

# 4) Veri kaynaklarına erişimi test et
python app.py --check-sources

# 5) Web arayüzünü başlat
python app.py --serve
# → http://127.0.0.1:5000
```

Arayüzden birkaç hisse ekle (örn. `THYAO, GARAN, ASELS`), sonra **"Önizleme
oluştur"** düğmesiyle e-posta göndermeden raporun nasıl görüneceğine bak.
İyi görünüyorsa **"Şimdi gönder"** ile gerçek e-postayı tetikle.

**Gereksinim:** Python 3.10 veya üzeri.

---

## 2. Komutlar

| Komut | Ne yapar |
|---|---|
| `python app.py --serve` | Web arayüzünü başlatır (varsayılan mod) |
| `python app.py --run-now` | Raporu şimdi üretir ve e-posta gönderir |
| `python app.py --run-now --dry-run` | Gönderme; HTML'i `outbox/` klasörüne yazar |
| `python app.py --run-now --symbols THYAO GARAN` | Sadece belirtilen hisseler için |
| `python app.py --schedule` | Zamanlayıcıyı çalıştırır (süreç açık kalır) |
| `python app.py --serve --schedule` | Arayüz + zamanlayıcı birlikte |
| `python app.py --self-test` | Ağ gerektirmeyen duman testi |
| `python app.py --check-sources` | Her veri kaynağına erişimi tek tek test eder |
| `python app.py --test-email` | SMTP ayarlarını test e-postasıyla doğrular |
| `python app.py --add THYAO GARAN` | Komut satırından hisse ekler |
| `python app.py --add TUPRS --target 195 --alert 3` | Hedef fiyat/alarm ile ekler |
| `python app.py --remove THYAO` | Hisse çıkarır |
| `python app.py --list` | Takip listesini yazdırır |

Hafta sonu rapor gönderilmez (`SKIP_WEEKENDS=true`). Test ederken bu kuralı
`--force` ile atlayabilirsin.

---

## 3. E-posta (SMTP) Ayarları

Tüm kimlik bilgileri **yalnızca `.env` dosyasından** okunur; kodda hiçbir
şifre gömülü değildir. `.env` dosyası `.gitignore`'da hariç tutulmuştur.

### Gmail Uygulama Şifresi (App Password)

Gmail normal hesap şifrenle SMTP bağlantısına izin vermez. Şu adımları izle:

1. Google Hesabı → **Güvenlik** → **2 Adımlı Doğrulama**'yı aç (zorunlu ön koşul).
2. Aynı sayfada **Uygulama şifreleri** bölümüne gir
   (doğrudan: `https://myaccount.google.com/apppasswords`).
3. Uygulama adı olarak "BIST Takip" gibi bir şey yaz ve oluştur.
4. Ekranda çıkan **16 haneli şifreyi** kopyala (`abcd efgh ijkl mnop` gibi).
5. `.env` dosyasına yapıştır:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=senin.adresin@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop
SMTP_USE_TLS=true
SMTP_USE_SSL=false
MAIL_FROM=senin.adresin@gmail.com
MAIL_TO=senin.adresin@gmail.com
```

Ardından doğrula:

```bash
python app.py --test-email
```

### Diğer sağlayıcılar

| Sağlayıcı | Host | Port | Ayar |
|---|---|---|---|
| Gmail | `smtp.gmail.com` | 587 | `SMTP_USE_TLS=true` |
| Outlook / Office 365 | `smtp.office365.com` | 587 | `SMTP_USE_TLS=true` |
| Yandex | `smtp.yandex.com.tr` | 465 | `SMTP_USE_SSL=true`, `SMTP_USE_TLS=false` |
| Yahoo | `smtp.mail.yahoo.com` | 465 | `SMTP_USE_SSL=true`, `SMTP_USE_TLS=false` |

Birden fazla alıcı için virgülle ayır: `MAIL_TO=ben@x.com, esim@y.com`

---

## 4. Zamanlama

İki yöntem de destekleniyor; ihtiyacına göre birini seç.

### Yöntem A — Uygulamanın kendi zamanlayıcısı

Süreç açık kaldığı sürece `DAILY_SEND_TIME`'da (varsayılan **18:30 TSİ**,
piyasa kapanışından sonra) raporu gönderir.

```bash
python app.py --serve --schedule
```

Makinenin saat dilimi Europe/Istanbul değilse saat otomatik olarak
kaydırılır — `TIMEZONE` ayarı bunun için var.

Gün içi ek kontroller istersen `.env`'de:

```ini
INTRADAY_ENABLED=true
INTRADAY_TIMES=11:00,15:00
```

**Dezavantajı:** bilgisayar kapalıysa ya da süreç düşerse gönderim atlanır.

### Yöntem B — Sistem zamanlayıcısı (daha güvenilir)

**Linux / macOS (cron)** — `crontab -e` ile ekle:

```cron
# Hafta içi her gün TSİ 18:30'da çalıştır
30 18 * * 1-5 cd /tam/yol/bist_takip && /tam/yol/bist_takip/.venv/bin/python app.py --run-now >> logs/cron.log 2>&1
```

> Sunucunun saat dilimi UTC ise saati ona göre yaz (TSİ 18:30 = UTC 15:30).

**macOS (launchd)** kullanmayı tercih edersen `~/Library/LaunchAgents/` altına
bir `.plist` koyabilirsin; cron da macOS'ta çalışır.

**Windows (Görev Zamanlayıcı)**

1. Görev Zamanlayıcı → **Temel Görev Oluştur**
2. Tetikleyici: *Günlük*, saat **18:30**
3. Eylem: *Program başlat*
   - Program: `C:\yol\bist_takip\.venv\Scripts\python.exe`
   - Bağımsız değişkenler: `app.py --run-now`
   - Başlangıç konumu: `C:\yol\bist_takip`

PowerShell ile tek satırda:

```powershell
schtasks /create /tn "BIST Takip" /tr "C:\yol\bist_takip\.venv\Scripts\python.exe C:\yol\bist_takip\app.py --run-now" /sc daily /st 18:30
```

---

## 5. Veri Kaynakları

Fiyat için iki sağlayıcı **öncelik sırasıyla** denenir; ilki düşerse ikincisi
devreye girer. Sırayı `.env`'deki `PRICE_PROVIDERS` ile değiştirebilirsin.

| Kaynak | Ne sağlar | Notlar |
|---|---|---|
| **borsapy** | Fiyat, OHLCV, KAP bildirimleri, analist hedefleri, tavsiyeler | BIST'e özel. Veri TradingView (~15 dk gecikmeli) + İş Yatırım + KAP kaynaklı |
| **yfinance** | Fiyat, OHLCV | Yedek. BIST kodları `.IS` son ekiyle sorgulanır (`THYAO.IS`) |
| **KAP resmî API** | Şirket bildirimleri | Birincil bildirim kaynağı. Tek istekte tüm bildirimler alınıp filtrelenir |
| **Google News RSS** | Genel haberler | Yedek/tamamlayıcı. Hisse kodu + şirket adıyla filtrelenir |

**Neden borsapy?** İstediğin dört veri türünden üçünü (fiyat, KAP bildirimleri,
analist hedef fiyatları) tek kütüphaneden karşılıyor ve BIST'e özel olduğu için
`.IS` son eki, lot/TL hacim ayrımı gibi yerel ayrıntıları doğru ele alıyor.

> **Lisans uyarısı:** borsapy "kişisel ve eğitim amaçlı kullanım" ile sınırlıdır.
> Ticari kullanım için Borsa İstanbul'dan izin alman gerekir. Kişisel portföy
> takibi için sorun yok.

### Kaynak erişimini test etme

```bash
python app.py --check-sources
```

Her kaynağı tek tek dener ve ✓/✗ olarak raporlar. Kurumsal ağ, VPN veya proxy
arkasındaysan hangi kaynağın engellendiğini burada görürsün. Fiyat
sağlayıcılarından **en az biri** çalışıyorsa uygulama iş görür.

---

## 6. Alarmlar

Her hisse için iki tür alarm tanımlanabilir:

- **Yüzde eşiği** (`alert_pct`) — günlük değişim ±%X'i aşarsa e-postada
  vurgulanır. Hisse için ayrı değer girilmemişse `DEFAULT_ALERT_PCT` (varsayılan %5) kullanılır.
- **Hedef fiyat** (`target_price`) — fiyat hedefe ulaşırsa ya da %3'ten
  yakınsa uyarır.

Tetiklenen tüm alarmlar e-postanın en üstünde ayrı bir kutuda özetlenir ve
ilgili hissenin başlığında `ALARM` rozetiyle işaretlenir.

---

## 7. Proje Yapısı

```
bist_takip/
├── BIST-Takip-Baslat.bat        # Tek tıkla başlatıcı (Windows)
├── Masaustu-Kisayol-Olustur.bat # Masaüstü kısayolu oluşturur
├── scripts/
│   ├── launcher.ps1     # Başlatıcı mantığı: kurulum + çalıştırma + tarayıcı
│   └── create-shortcut.ps1
├── app.py               # CLI giriş noktası — tüm modları yönetir
├── webapp.py            # Flask arayüzü + arka plan iş yöneticisi
├── config.py            # .env okuma, tüm ayarlar tek dataclass'ta
├── db.py                # SQLite: takip listesi, çalıştırma logu, haber tekrarı
├── report.py            # Orkestrasyon: veri toplama + alarm değerlendirme
├── runner.py            # Tek giriş noktası: rapor üret → gönder → logla
├── mailer.py            # HTML/düz metin render + smtplib gönderim
├── scheduler.py         # schedule tabanlı zamanlayıcı (saat dilimi çevirisiyle)
├── formatting.py        # Türkçe sayı biçimlendirme (1.234,56 TL)
├── logging_setup.py     # Konsol + döngüsel dosya logu
├── selftest.py          # Ağ gerektirmeyen duman testi
├── providers/
│   ├── base.py          # FetchResult, PriceData, NewsItem, StockReport tipleri
│   ├── prices.py        # borsapy → yfinance fiyat zinciri
│   ├── kap.py           # KAP resmî API + borsapy yedeği
│   ├── news.py          # Google News RSS + kural tabanlı özetleme
│   ├── analysts.py      # Analist hedef fiyatları ve tavsiyeler
│   └── symbols.py       # Kod ↔ şirket adı, BIST 100 listesi
├── templates/
│   ├── base.html        # Arayüz iskeleti (Bootstrap yerel)
│   ├── index.html       # Takip listesi yönetimi
│   ├── error.html
│   └── email.html       # HTML e-posta şablonu (satır içi stiller)
├── static/vendor/       # Bootstrap 5 (yerel — internet olmadan da çalışır)
├── data/bist.db         # SQLite (ilk çalıştırmada oluşur)
├── logs/bist.log        # Döngüsel log (2 MB × 5 dosya)
└── outbox/              # --dry-run HTML önizlemeleri
```

---

## 8. Hata Yönetimi

Tasarım ilkesi: **hiçbir tekil hata tüm raporu düşürmez.**

- Her veri çekme işlemi `FetchResult` döner — ya veri ya da hata mesajı.
- Bir hissenin fiyatı alınamazsa e-postada o hisse için kırmızı
  "Fiyat verisi alınamadı" kutusu görünür, diğer hisseler normal işlenir.
- Bir sağlayıcı düşerse zincirdeki sıradaki denenir (borsapy → yfinance).
- Analist verisi bulunamazsa o bölüm hiç gösterilmez, rapor bloklanmaz.
- KAP'a erişilemezse bu açıkça "Bildirim verisi alınamadı" olarak yazılır —
  sessizce "bugün haber yok" **denmez**.
- Tüm hatalar `logs/bist.log` dosyasına ve arayüzdeki "Son çalıştırmalar"
  tablosuna kaydedilir.

---

## 9. Sık Karşılaşılan Sorunlar

**`SMTPAuthenticationError` alıyorum**
Gmail'de normal şifre çalışmaz; Uygulama Şifresi oluşturman gerekiyor (bölüm 3).
2 Adımlı Doğrulama açık değilse Uygulama Şifreleri menüsü görünmez.

**Fiyat verisi hiç gelmiyor**
`python app.py --check-sources` çalıştır. İkisi de ✗ ise ağ/proxy engeli var
demektir. Kurumsal ağdaysan VPN'i kapatıp dene.

**`yfinance: possibly delisted` uyarısı**
Yahoo bazı BIST kodlarını tanımıyor ya da geçici olarak yanıt vermiyor.
borsapy birincil kaynak olduğu için genelde sorun olmaz; sırayı
`PRICE_PROVIDERS=borsapy,yfinance` olarak bırak.

**E-posta boş geliyor / haber yok**
Aynı bildirim iki kez gönderilmesin diye gönderilenler `sent_news` tablosunda
işaretlenir. Testte hep aynı haberleri görmek istersen `data/bist.db` dosyasını
silebilirsin (takip listesi de silinir) ya da `--dry-run` kullan — dry-run
gönderim işareti koymaz.

**Arayüzde "Şimdi gönder" düğmesi pasif**
SMTP ayarları eksik. Sağ üstteki E-posta kutusunda hangi alanların eksik
olduğu yazıyor.

**Rapor hafta sonu gelmiyor**
Tasarım gereği (`SKIP_WEEKENDS=true`). Kapatmak için `.env`'de `false` yap.

---

## 10. Güvenlik Notları

Uygulama bir güvenlik incelemesinden geçirildi ve sertleştirildi. Bu bölüm
hem yapılan savunmaları hem de senin dikkat etmen gerekenleri anlatır.

### 10.1 Hızlı kontrol listesi

Kurulumdan sonra bunlardan emin ol:

- [ ] `.env` dosyasının izni `600` (uygulama başlangıçta otomatik düzeltir)
- [ ] `.env` dosyası hiçbir yere commit'lenmemiş (`.gitignore`'da hariç tutuldu)
- [ ] `FLASK_SECRET_KEY` satırı **boş** — uygulama güçlü bir anahtar üretir
- [ ] `FLASK_HOST=127.0.0.1` (arayüzü dışarı açmıyorsan)
- [ ] `FLASK_DEBUG=false`
- [ ] Gmail için normal şifre değil **Uygulama Şifresi** kullanılıyor
- [ ] `pip install -U pip setuptools` çalıştırıldı

### 10.2 Yerleşik savunmalar

Bunları senin yapmana gerek yok; uygulama otomatik uygular.

| Savunma | Ne yapar |
|---|---|
| **CSRF token'ları** | Tüm form gönderimleri oturuma bağlı token gerektirir. Ziyaret ettiğin kötü niyetli bir sitenin arka planda takip listeni değiştirmesini engeller. |
| **Origin/Referer kontrolü** | Cross-site POST istekleri token'dan bağımsız olarak da reddedilir. |
| **`SameSite=Strict` çerez** | Oturum çerezi cross-site isteklerde hiç gönderilmez. |
| **Host başlığı doğrulaması** | DNS rebinding saldırılarını engeller. `127.0.0.1`'e bağlanmak tek başına bunu engellemez. |
| **Güvenlik başlıkları** | `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. |
| **URL şeması doğrulaması** | Dış kaynaklardan gelen haber bağlantıları yalnızca `http`/`https` ise link olur. `javascript:` gibi şemalar düz metne çevrilir. |
| **HTML kaçırma (autoescape)** | Haber başlıkları ve KAP metinleri şablona kaçırılarak yazılır; XSS engellenir. |
| **Parametreli SQL** | Tüm sorgular `?` yer tutucu kullanır; string birleştirme yok. |
| **Girdi doğrulama** | Hisse kodları `^[A-Z0-9]{3,6}$` kalıbına ve mümkünse canlı BIST listesine karşı doğrulanır. Sayısal alanlarda `NaN`/sonsuz/aşırı değerler elenir. |
| **Dosya izinleri** | `.env`, `data/` ve `logs/` başlangıçta `600`/`700`'e sıkılaştırılır. |
| **Güçlü secret key** | Tanımlı değilse ya da zayıfsa `secrets.token_urlsafe(48)` ile üretilip `data/secret_key` dosyasına `600` izinle yazılır. |
| **Timeout'lar** | Tüm dış istekler zaman aşımlıdır; ayrıca tüm rapor çalıştırması için üst süre sınırı vardır. Asılı bir kaynak zamanlayıcıyı donduramaz. |
| **Hız sınırlama** | Dış kaynaklara istekler arasında otomatik gecikme; eşzamanlılık 8 ile sınırlı. |
| **Hata gizleme** | Tarayıcıya ve e-postaya ham istisna metni, dosya yolu veya yığın izi yazılmaz. Ayrıntı yalnızca log dosyasında. |

### 10.3 Arayüzü dışarı açacaksan

Varsayılan yapılandırma (`FLASK_HOST=127.0.0.1`) yalnızca kendi bilgisayarından
erişime izin verir ve önerilen kullanım budur.

`FLASK_HOST`'u değiştirirsen uygulama **kimlik doğrulama olmadan başlamayı
reddeder**. `.env`'de iki seçenekten birini tanımla:

**Seçenek A — token** (tek kullanıcı için pratik):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
```ini
AUTH_TOKEN=uretilen-degeri-buraya-yapistir
```
Tarayıcıda ilk girişte `http://adres:5000/?token=DEGER` kullan; sonrası oturumda tutulur.

**Seçenek B — kullanıcı adı + şifre** (HTTP Basic):

```ini
AUTH_USER=kullanici
AUTH_PASSWORD=en-az-12-karakterlik-guclu-bir-sifre
```

Ayrıca ağ adıyla erişeceksen Host izinli listesine ekle:

```ini
ALLOWED_HOSTS=bist.evim.lan
```

**HTTPS olmadan internete açma.** HTTP Basic kimlik bilgileri ve oturum
çerezi şifrelenmeden gider. Bir ters proxy (Caddy, nginx) arkasına koy ve:

```ini
SESSION_COOKIE_SECURE=true
```

Gerçekten uzaktan erişmen gerekiyorsa en güvenli yol uygulamayı internete hiç
açmamak, bunun yerine bir VPN (WireGuard, Tailscale) ya da SSH tüneli
kullanmaktır:

```bash
ssh -L 5000:127.0.0.1:5000 kullanici@makinen
```

### 10.4 Neden `FLASK_DEBUG=false`

`FLASK_DEBUG=true` Werkzeug'un etkileşimli hata ayıklayıcısını açar; bu
ayıklayıcı **tarayıcıdan Python kodu çalıştırmaya izin verir**. Uygulama,
localhost dışına bağlıyken debug modunu zorla kapatır, ama sen de açık
bırakma.

### 10.5 SMTP kimlik bilgileri

- Gmail'de **normal hesap şifreni kullanma**; Uygulama Şifresi oluştur (bölüm 3).
  Uygulama Şifresi ele geçirilirse yalnızca onu iptal edersin, hesabın etkilenmez.
- Uygulama şifreyi yalnızca `.env`'den okur; kodda, log'da ya da e-postada
  hiçbir yerde görünmez.
- Şifre değiştirmek için `.env`'i güncelleyip uygulamayı yeniden başlat.

### 10.6 Bağımlılık güvenliği

Bağımlılıklar `pip-audit` ile denetlendi; çalışma zamanı paketlerinde bilinen
açık yok. Periyodik olarak tekrarla:

```bash
pip install pip-audit
pip-audit
pip install -U pip setuptools   # ortam araçlarını güncel tut
```

`requirements.txt`'teki alt sınırlar güvenlik yamalarını içeren sürümlere göre
belirlenmiştir; bunları düşürme.

### 10.7 Bilinen sınırlar

Dürüst olmak gerekirse bu araç kişisel kullanım için tasarlandı ve şunları
yapmaz:

- **Çok kullanıcılı erişim yok.** Kimlik doğrulama tek bir paylaşılan
  token/şifredir; kullanıcı ayrımı, rol ya da denetim izi yoktur.
- **Brute-force koruması yok.** Kimlik doğrulama denemelerinde hız sınırı ya da
  hesap kilitleme uygulanmaz. İnternete açmak yerine VPN kullan.
- **Şifreli veri saklama yok.** SQLite dosyası şifresizdir; koruma yalnızca
  dosya sistemi izinleridir. Disk şifrelemesi kullanman önerilir.
- **Dış veri kaynaklarına güven.** KAP, Google News ve fiyat sağlayıcılarından
  gelen veri doğrulanır ve kaçırılır, ancak **doğruluğu** teyit edilemez.
  Yatırım kararlarını resmî kaynaktan doğrula.

### 10.8 Güvenlik testlerini çalıştırma

Sertleştirmelerin çalıştığını doğrulayan regresyon testleri `--self-test`
içinde yer alır (ağ gerektirmez):

```bash
python app.py --self-test
```

URL şeması doğrulaması, hisse kodu filtreleme, e-posta adresi doğrulaması,
hata metni temizleme ve secret key üretimi bu testlerle kontrol edilir.

Statik analiz ve bağımlılık denetimi için:

```bash
pip install bandit pip-audit
bandit -r .      # kalan bulgular incelendi ve yanlış pozitif olarak doğrulandı
pip-audit
```

---

## Sorumluluk Reddi

Bu araç kişisel takip amaçlıdır ve **yatırım tavsiyesi değildir**. Fiyat
verileri gecikmeli olabilir; analist hedef fiyatları üçüncü taraf kaynaklardan
derlenmiştir. Yatırım kararlarını resmî kaynaklardan doğrula.

## Telif Hakkı / Copyright

© 2026 Yağız Ali Küçük. Tüm hakları saklıdır.

Bu depodaki kaynak kod ve içerik yalnızca incelenmek üzere herkese açık paylaşılmıştır. Yazılı izin olmadan kopyalanamaz, değiştirilemez, dağıtılamaz veya başka bir projede kullanılamaz.

This repository is publicly visible for reference only. No license is granted: the source code and content may not be copied, modified, distributed, or used in other projects without written permission.
