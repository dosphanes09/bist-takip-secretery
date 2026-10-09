# BIST Takip — Nasıl Çalışıyor?

Bu belge projenin tamamını anlatır: ne yaptığını, hangi parçadan oluştuğunu,
verinin nereden gelip nereye gittiğini ve bir şeyi değiştirmek istediğinde
nereye bakman gerektiğini.

Kod okumayı bilmene gerek yok. Her bölüm önce "ne yapıyor", sonra "neden böyle"
diye ilerliyor.

---

## 1. Tek Cümlede

Bilgisayarında çalışan, takip ettiğin BIST hisseleri için her gün fiyat, KAP
bildirimi, haber ve analist hedef fiyatı toplayıp sana tek bir HTML e-posta
olarak gönderen bir program.

Bulut yok, abonelik yok, üçüncü tarafa veri gitmiyor. Her şey senin makinende.

---

## 2. Büyük Resim

Üç ayrı "an" var. Karıştırmamak önemli, çünkü sorun çıktığında hangisinde
çıktığını bilmek çözümü yarı yarıya kolaylaştırıyor.

```
┌─────────────────────────────────────────────────────────────┐
│  AN 1 — SEN LİSTEYİ DÜZENLERSİN  (istediğin zaman)          │
│                                                              │
│   Tarayıcı ──► Flask web arayüzü ──► SQLite (bist.db)        │
│   localhost:5000                      takip listesi          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  AN 2 — PROGRAM VERİ TOPLAR  (her gün 18:30, ya da elle)     │
│                                                              │
│   SQLite'tan liste oku                                       │
│        │                                                     │
│        ├─► borsapy ──(olmazsa)──► yfinance      = FİYAT      │
│        ├─► KAP resmî API ──(olmazsa)──► borsapy = BİLDİRİM   │
│        ├─► Google News RSS                      = HABER      │
│        └─► borsapy (hedeffiyat.com.tr)          = ANALİST    │
│                                                              │
│   Her hisse ayrı ayrı, paralel (aynı anda 4 tane)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  AN 3 — E-POSTA GİDER                                        │
│                                                              │
│   Toplanan veri ──► HTML şablonu ──► Gmail SMTP ──► Kutun    │
│                                                              │
│   Başarılıysa: haberler "gönderildi" işaretlenir             │
│   (böylece yarın aynı haber tekrar gelmez)                   │
└─────────────────────────────────────────────────────────────┘
```

**Kritik nokta:** 2. an ile 3. an ayrıdır. Veri toplama başarılı olup e-posta
gönderimi başarısız olabilir. Programın ilk sürümünde bu ayrım yanlış
kurulmuştu ve gönderim çökünce haberler kayboluyordu — düzeltildi (bkz. bölüm 10).

---

## 3. Dosyalar ve Görevleri

Proje "modüler" — yani her dosya tek bir işten sorumlu. Bunun pratik faydası:
haberlerle ilgili bir şeyi değiştirmek istediğinde yalnızca bir dosyaya
bakarsın, fiyat kodunu yanlışlıkla bozma riskin olmaz.

### Giriş noktaları

| Dosya | Görevi |
|---|---|
| `app.py` | Komut satırı. `--serve`, `--run-now`, `--add` gibi tüm komutlar burada tanımlı. Program hep buradan başlar. |
| `webapp.py` | Flask web arayüzü. Hisse ekleme/çıkarma sayfası, "Şimdi gönder" düğmesi. |
| `runner.py` | "Bir raporluk iş"in tamamını yürüten yer: veri topla → e-posta gönder → sonucu kaydet. |
| `scheduler.py` | Saat 18:30'u bekleyip `runner`'ı tetikleyen zamanlayıcı. |

### Veri toplama (`providers/` klasörü)

| Dosya | Görevi |
|---|---|
| `providers/prices.py` | Fiyat. Önce borsapy dener, olmazsa yfinance. |
| `providers/kap.py` | KAP bildirimleri. Resmî API'den tek seferde tüm bildirimleri indirip hisselere dağıtır. |
| `providers/news.py` | Google News RSS. Haber başlığı + kısa özet. |
| `providers/analysts.py` | Analist hedef fiyatı ve AL/TUT/SAT tavsiyesi. |
| `providers/symbols.py` | "THYAO" → "Türk Hava Yolları" çevirisi, BIST 100 listesi. |
| `providers/base.py` | Ortak tipler. Tüm sağlayıcılar aynı kalıpta sonuç döndürsün diye. |

### Destek

| Dosya | Görevi |
|---|---|
| `config.py` | `.env` dosyasını okur. Tüm ayarlar tek yerde toplanır. |
| `db.py` | SQLite işlemleri. Takip listesi, çalıştırma geçmişi, gönderilmiş haberler. |
| `report.py` | Sağlayıcılardan gelen parçaları birleştirip alarmları hesaplar. |
| `mailer.py` | HTML e-postayı üretir ve SMTP ile gönderir. |
| `security.py` | Web arayüzünün güvenlik katmanı (bölüm 8). |
| `formatting.py` | Sayıları Türkçe biçimde yazar: `1.234,56 TL`. |
| `selftest.py` | İnternetsiz çalışan test paketi. |
| `templates/email.html` | E-postanın görünümü. |
| `templates/index.html` | Web arayüzünün görünümü. |

---

## 4. Veri Kaynakları — Ne Nereden Geliyor

### Fiyat: borsapy → yfinance

**borsapy**, Türk piyasalarına özel bir Python kütüphanesi. Verisini
TradingView'den (yaklaşık 15 dakika gecikmeli) ve İş Yatırım'dan alıyor.
`THYAO` gibi sade kodlarla çalışıyor.

**yfinance** (Yahoo Finance) yedek. BIST hisselerini `.IS` son ekiyle tanıyor:
`THYAO.IS`.

Zincir şöyle işliyor: borsapy denenir, hata verirse ya da fiyat boş gelirse
yfinance denenir. İkisi de olmazsa o hisse için e-postada kırmızı "veri
alınamadı" kutusu görünür — **rapor durmaz**, diğer hisseler normal işlenir.

> Senin makinende yapılan 26 çekmenin 25'i borsapy'den geldi, 1'i yfinance'a
> düştü. Yedek zincir gerçekten iş görüyor.

### KAP bildirimleri

KAP (Kamuyu Aydınlatma Platformu), halka açık şirketlerin resmî açıklamalarını
yayımlamak zorunda olduğu devlet platformu. Sermaye artırımı, temettü, yönetim
değişikliği, finansal rapor — hepsi buradan duyurulur. Haber sitelerinden
**daha güvenilir ve daha hızlıdır**, çünkü kaynağın kendisidir.

Program KAP'ın resmî sorgu API'sine **tek istek** atıp o günün tüm
bildirimlerini indiriyor (günde ~590 adet), sonra takip listendeki hisselere
göre filtreliyor. Hisse başına ayrı istek atmıyor — hem hızlı hem de KAP'a
saygılı.

> Bu tasarımın faydası ölçüldü: 4 hisse için 4 istek yerine 1 istek.
> 50 hisselik bir listede fark 50 kat.

Bir hisse için "0 bildirim" görmen normaldir; şirketler her gün açıklama
yapmaz. Örnek bir çalıştırmada GARAN 4, THYAO 1, ASELS ve TUPRS 0 bildirim
aldı — hepsi doğru.

### Haberler: Google News RSS

KAP resmî ama dar. Genel haberler için Google News'in RSS akışı kullanılıyor.
Sorgu, hisse kodu **ve** şirket adıyla daraltılıyor:

```
"THYAO" OR "Türk Hava Yolları"
```

Gelen haberler son 36 saatle sınırlanıyor ve başlıkta/özette kod ya da şirket
adı geçmeyenler eleniyor — yoksa alakasız içerik doluyor.

**Özetler kural tabanlı:** RSS'ten gelen metin temizlenip ilk 2 cümlesi
alınıyor. Yapay zekâ kullanılmıyor; bedava, offline ve deterministik. Ayrıca
özet sadece başlığın tekrarıysa hiç gösterilmiyor.

### Analist hedef fiyatları

borsapy üzerinden `hedeffiyat.com.tr` ve İş Yatırım'dan geliyor: ortalama hedef
fiyat, en düşük–en yüksek aralık, kaç analistin kapsadığı, AL/TUT/SAT dağılımı
ve mevcut fiyata göre yükseliş potansiyeli.

Bu bölüm tamamen opsiyonel — veri gelmezse kutu hiç gösterilmiyor, rapor
etkilenmiyor.

---

## 5. Hata Felsefesi — Neden Hiçbir Şey Çökmüyor

Bu projenin en önemli tasarım kararı şu: **tek bir hata tüm raporu
düşürmemeli.**

Bunu sağlayan mekanizma `providers/base.py` içindeki `FetchResult` yapısı. Her
veri çekme işlemi ya "başarılı + veri" ya da "başarısız + hata mesajı"
döndürüyor. Hiçbiri programı durduran bir istisna fırlatmıyor.

Pratikte şu anlama geliyor:

- THYAO'nun fiyatı alınamazsa → sadece THYAO'da kırmızı kutu, diğer üçü normal
- Google News çökerse → haber bölümü boş, fiyat ve KAP yerinde
- Analist verisi yoksa → o kutu hiç görünmez
- KAP'a ulaşılamazsa → "Bildirim verisi alınamadı" yazar

Son madde önemli: KAP'a ulaşılamadığında program **sessizce "bugün bildirim
yok" demez**. Bu ayrım bilinçli — sessiz başarısızlık, yanlış bilgi vermekle
aynı şeydir.

Ayrıca üç koruma var:

- **Zaman aşımı:** her dış istek en fazla 20 saniye bekler
- **Genel süre sınırı:** tüm rapor en fazla 10 dakika sürer, sonra elde olanla
  devam eder (yanıt vermeyen bir sunucu zamanlayıcıyı sonsuza kadar donduramaz)
- **Hız sınırı:** istekler arasında otomatik gecikme, aynı anda en fazla 8 iş

---

## 6. Veritabanı — Üç Tablo

`data/bist.db` dosyası. SQLite, yani kurulum gerektirmeyen tek dosyalık bir
veritabanı. Sunucu yok, servis yok.

| Tablo | İçeriği |
|---|---|
| `watchlist` | Takip listen: hisse kodu, şirket adı, hedef fiyat, alarm eşiği, not, aktif mi |
| `run_log` | Her çalıştırmanın kaydı: ne zaman, kaç hisse, kaç başarısız, e-posta gitti mi |
| `sent_news` | Daha önce gönderilmiş haber ve bildirimlerin listesi |

`sent_news` tablosu şunu çözüyor: aynı KAP bildirimi üç gün üst üste e-postana
düşmesin. Her haberin adresi (URL) kaydediliyor, bir sonraki raporda o adres
zaten varsa haber atlanıyor. 45 günden eski kayıtlar otomatik siliniyor.

Yanlışlıkla işaretlenmiş haberleri geri getirmek istersen:

```powershell
.\.venv\Scripts\python.exe app.py --reset-news
```

---

## 7. Alarmlar

İki tür alarm var, ikisi de hisse bazında ayarlanabilir:

**Yüzde eşiği** — günlük değişim ±%X'i aşarsa. Hisse için ayrı değer
girmezsen `.env`'deki `DEFAULT_ALERT_PCT` (varsayılan %5) kullanılır.

**Hedef fiyat** — fiyat senin girdiğin hedefe ulaşırsa ya da %3'ten
yakınlaşırsa uyarır.

Tetiklenen alarmlar e-postanın en üstünde sarı bir kutuda toplanıyor, ilgili
hissenin başlığına da `ALARM` rozeti düşüyor.

Alarmlar `report.py` içindeki `_evaluate_alerts()` fonksiyonunda hesaplanıyor —
yeni bir alarm türü eklemek istersen orası.

---

## 8. Güvenlik Katmanı

Uygulama yalnızca `127.0.0.1` (kendi bilgisayarın) üzerinden erişilebilir
durumda. Ama "sadece localhost" tek başına güvenlik değil — bunu bir güvenlik
incelemesiyle test ettik ve iki gerçek açık bulduk.

**CSRF (siteler arası istek sahteciliği).** Tarayıcılar farklı sitelerden
gelen form gönderimlerine izin verir. Yani sen uygulamayı açıkken kötü niyetli
bir siteyi ziyaret etsen, o site arka planda senin takip listenden hisse
sildirebilirdi. Bunu gerçekten sömürerek kanıtladık: farklı bir origin'den
gönderilen istek GARAN'ı listeden sildi. Artık her form gizli bir güvenlik
belirteci taşıyor ve dışarıdan gelen istekler 403 ile reddediliyor.

**DNS rebinding.** Saldırgan kendi alan adını önce kendi sunucusuna, saniyeler
sonra `127.0.0.1`'e yönlendirerek tarayıcını kandırabilir; tarayıcı artık senin
yerel uygulamanı "aynı site" sayar ve saldırganın kodu veriyi okuyabilir.
Loopback'e bağlanmak bunu engellemez. Tek çözüm gelen isteğin `Host` başlığını
doğrulamak — artık doğruluyoruz, sahte başlık 400 alıyor.

Bunların yanında:

- Tüm SQL sorguları parametreli (SQL enjeksiyonu mümkün değil)
- Haber başlıkları HTML'e kaçırılarak yazılıyor (XSS engelleniyor)
- Dış kaynaklardan gelen bağlantılar yalnızca `http`/`https` ise link oluyor
  (`javascript:` gibi şemalar düz metne çevriliyor)
- `.env` ve `data/` dosyaları başlangıçta yalnızca sana açık izne çekiliyor
- Hata mesajları tarayıcıya dosya yolu ya da yığın izi sızdırmıyor
- Arayüzü dışarı açmaya kalkarsan uygulama **kimlik doğrulama olmadan
  başlamayı reddediyor**

Ayrıntılar `README.md` bölüm 10'da.

---

## 9. E-posta Nasıl Üretiliyor

`templates/email.html` bir şablon. İçindeki `{{ ... }}` yerlerine veriler
yerleştiriliyor. Tüm stiller satır içi yazılmış, çünkü Gmail ve Outlook gibi
istemciler ayrı stil bloklarını sıklıkla siliyor.

Her e-posta iki gövde taşıyor: HTML ve düz metin. HTML görüntüleyemeyen
istemciler ikincisini okuyor; ayrıca spam filtreleri çift gövdeli mesajlara
daha olumlu bakıyor.

Gönderim Python'un standart `smtplib` kütüphanesiyle, Gmail'in SMTP sunucusu
üzerinden yapılıyor. Kimlik bilgileri **yalnızca** `.env` dosyasından okunuyor;
kodda, log'da ya da e-postanın kendisinde hiçbir yerde görünmüyor.

**Uygulama Şifresi neden gerekiyor:** Google 2019'dan beri normal hesap
şifresiyle üçüncü taraf SMTP bağlantısına izin vermiyor. Uygulama Şifresi
sadece bu program için geçerli, istediğin an iptal edebileceğin ayrı bir
şifre. Ele geçirilse bile Gmail hesabına giremezler.

---

## 10. Yolda Bulduğumuz Hatalar

Bunları yazıyorum çünkü hepsi öğretici ve hepsi gerçek kullanımda ortaya çıktı.
Bir daha benzer bir şey olduğunda nereye bakacağını bilirsin.

| Hata | Neden oldu | Nasıl bulundu |
|---|---|---|
| Zamanlayıcı başlar başlamaz çöküyordu | `schedule` kütüphanesinde `next_run` modül düzeyinde fonksiyon, nesne düzeyinde özellik — ikisini karıştırdım | Zamanlayıcıyı gerçekten çalıştırarak |
| KAP'a ulaşılamayınca "bugün bildirim yok" deniyordu | Yedek kütüphane hata fırlatmak yerine sessizce boş sonuç döndürüyor | Ağı kapalı ortamda tam raporu çalıştırarak |
| Önceki kapanış hep boştu | borsapy alanı `prev_close` adıyla döndürüyor, ben dokümantasyondaki `close` adını okumuştum | Senin ilk ekran görüntün |
| Haber özeti başlığın kopyasıydı | Google News RSS'in özet alanı sadece başlığı içeren bir bağlantı | Senin ilk ekran görüntün |
| PowerShell betiği hiç çalışmadı | Dosyada UTF-8 BOM yoktu; Windows PowerShell 5.1 dosyayı Türkçe kod sayfası sanıp Türkçe karakterleri bozdu ve parser çöktü | Senin hata mesajın |
| E-posta gönderimi çöküyordu | Modern e-posta API'sine eski API'nin `Header` nesnesini verdim | Senin hata mesajın |
| Şablon `.env` değerleri gerçek sanılıyordu | "Boş mu" kontrolü yetersizdi; `ornek@gmail.com` boş değil | Gmail'in kimlik doğrulama hatası |
| Gönderim çökünce haberler kayboldu | Haberler gönderim **başarılı olmadan** "gönderildi" işaretleniyordu | Log'da "12 haber" → "0 haber" düşüşü |

Son üçü en öğretici olanı: **testlerim `build_message()` fonksiyonunu hiç
çağırmıyordu.** HTML üretimini test ediyordum ama gerçek e-posta nesnesinin
kurulmasını değil. O yüzden hata ancak sen gerçekten göndermeye kalkınca
ortaya çıktı. Düzelttikten sonra o testi ekledim ve kodu bilerek bozup testin
gerçekten yakaladığını doğruladım.

Şu an `--self-test` komutu internetsiz olarak şunları kontrol ediyor:
veritabanı işlemleri, alarm mantığı, sayı biçimlendirme, e-posta şablonu,
gerçek mesaj kurulumu, fiyat alanı türetme, haber tekrar filtresi, güvenlik
kontrolleri ve Windows başlatıcı dosyalarının kodlaması.

---

## 11. Günlük Kullanım

### Tek tıkla

Masaüstündeki **BIST Takip** simgesi arayüzü açar ve zamanlayıcıyı başlatır.
Pencere açık kaldığı sürece her gün 18:30'da rapor gider. Pencereyi kapatmak
uygulamayı durdurur.

**BIST Takip - Raporu Gonder** simgesi raporu hemen üretip yollar.

### Komutlarla

```powershell
cd "$HOME\bist_takip"

.\.venv\Scripts\python.exe app.py --serve          # arayüzü aç
.\.venv\Scripts\python.exe app.py --run-now        # raporu şimdi gönder
.\.venv\Scripts\python.exe app.py --run-now --dry-run   # göndermeden önizle
.\.venv\Scripts\python.exe app.py --list           # takip listesini yazdır
.\.venv\Scripts\python.exe app.py --add SASA       # hisse ekle
.\.venv\Scripts\python.exe app.py --remove SASA    # hisse çıkar
.\.venv\Scripts\python.exe app.py --check-sources  # veri kaynaklarını test et
.\.venv\Scripts\python.exe app.py --diagnose-kap   # KAP eşleştirmesini incele
.\.venv\Scripts\python.exe app.py --test-email     # SMTP'yi test et
.\.venv\Scripts\python.exe app.py --self-test      # internetsiz duman testi
.\.venv\Scripts\python.exe app.py --reset-news     # haber işaretlerini temizle
```

### Bir şey ters gittiğinde

1. `logs/bist.log` dosyasına bak — her şey oraya yazılıyor
2. `--check-sources` çalıştır — hangi kaynağın düştüğünü söyler
3. `--self-test` çalıştır — kodun kendisi sağlam mı, internetsiz kontrol eder

---

## 12. Bir Şeyi Değiştirmek İstersen

| İstediğin | Nereye bakılır |
|---|---|
| Gönderim saatini değiştirmek | `.env` → `DAILY_SEND_TIME` |
| Gün içi de rapor almak | `.env` → `INTRADAY_ENABLED=true`, `INTRADAY_TIMES` |
| Hafta sonu da rapor almak | `.env` → `SKIP_WEEKENDS=false` |
| Varsayılan alarm eşiği | `.env` → `DEFAULT_ALERT_PCT` |
| Daha fazla/az haber | `.env` → `NEWS_MAX_ITEMS`, `NEWS_LOOKBACK_HOURS` |
| KAP'ta daha geriye bakmak | `.env` → `KAP_LOOKBACK_DAYS` |
| Haberleri tamamen kapatmak | `.env` → `NEWS_ENABLED=false` |
| Birden fazla alıcıya göndermek | `.env` → `MAIL_TO=a@x.com, b@y.com` |
| **E-postanın görünümü** | `templates/email.html` |
| **Arayüzün görünümü** | `templates/index.html` |
| **Yeni alarm türü** | `report.py` → `_evaluate_alerts()` |
| **Yeni veri kaynağı** | `providers/` altına yeni dosya + `report.py`'de çağrı |
| **Farklı haber kaynağı** | `providers/news.py` |
| Endeks/döviz/fon eklemek | borsapy bunları da destekliyor; yeni sağlayıcı gerekir |

`.env` değişiklikleri için uygulamayı yeniden başlatman gerekiyor.

---

## 13. Bilinen Sınırlar

Dürüst olmak gerekirse:

- **Fiyatlar gecikmeli.** borsapy TradingView'den ~15 dakika gecikmeli veri
  alıyor. Gün sonu raporu için sorun değil, gün içi alım-satım için uygun değil.
- **Bu bir yatırım aracı değil.** Veri toplayıp özetliyor, tavsiye vermiyor.
  Analist hedef fiyatları üçüncü taraf kaynaklardan derleniyor, doğruluğu
  garanti edilemez.
- **Bilgisayar kapalıysa rapor gitmez.** Zamanlayıcı senin makinende çalışıyor.
  (Görev Zamanlayıcı'ya bağlamak bunu kısmen çözer — pencere gerekmez ama
  bilgisayar yine açık olmalı.)
- **Tek kullanıcı.** Kimlik doğrulama tek bir paylaşılan şifre; kullanıcı
  ayrımı, rol, denetim izi yok.
- **borsapy lisansı** kişisel ve eğitim amaçlı kullanımla sınırlı. Ticari
  kullanım için Borsa İstanbul'dan izin gerekiyor.
- **Dış kaynaklara bağımlı.** KAP, Google News ya da TradingView arayüzünü
  değiştirirse ilgili sağlayıcının güncellenmesi gerekir. `--check-sources`
  ve `--diagnose-kap` tam olarak bunu tespit etmek için var.

---

## 14. Sözlük

**SMTP** — E-posta gönderme protokolü. Gmail'in sunucusuna bağlanıp "şu
mesajı şu adrese yolla" demenin standart yolu.

**RSS** — Sitelerin yeni içeriklerini makine tarafından okunabilir biçimde
yayımladığı akış. Google News bunu sağlıyor.

**API** — Bir servisin program tarafından kullanılmak üzere sunduğu arayüz.
KAP'ın API'si sayesinde web sitesini "kazımak" yerine doğrudan veri
isteyebiliyoruz.

**SQLite** — Tek dosyada duran, sunucu gerektirmeyen veritabanı.

**Sanal ortam (.venv)** — Bu projenin kütüphanelerini sistem Python'undan
ayrı tutan klasör. Başka projeleri bozmamak için.

**Flask** — Python'da web arayüzü yazmaya yarayan hafif kütüphane.

**CSRF** — Siteler arası istek sahteciliği. Bölüm 8'de anlatıldı.

**Dry-run** — "Kuru çalıştırma". Gerçek işlemi yapmadan (e-posta göndermeden)
sonucun ne olacağını gösteren mod.

---

*Bu belge projeyle birlikte güncel tutulmalı. Kodda önemli bir değişiklik
yaparsan ilgili bölümü de güncelle.*
