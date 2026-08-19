# Bu Projeyi Tek Başına Yapsaydın

Merak ettiğin soru şuydu: hangi sırayla ilerlerdin, ne kadar sürerdi, zor olan
neydi.

Aşağısı tahmin değil — bu projede **gerçekten olan** şeylerin dökümü. Sekiz
ciddi hata çıktı; hepsi kayıtlı. Onlara bakarak zamanın nereye gittiğini
söyleyebiliyorum.

---

## 1. Önce büyüklük: ne inşa edildi

| | |
|---|---|
| Dosya | 32 (Python, HTML, CSS, JS, PowerShell, belge) |
| Python kodu | ~3.400 satır |
| Şablon + CSS + JS | ~1.900 satır |
| Dış veri kaynağı | 4 (borsapy, yfinance, KAP API, Google News) |
| Yedekli zincir | 2 (fiyat, bildirim) |
| API uç noktası | 12 |
| Test | 90+ kontrol, ağ gerektirmeyen |
| Belge | README (21 KB) + MIMARI (21 KB) |

Bu "hafta sonu projesi" büyüklüğünde değil. Ama bir kısmı isteğe bağlı —
hangisini atlayabileceğini aşağıda ayrıca yazdım.

---

## 2. Zaman tahmini — iki sütun

Sütunlar deneyim seviyesine göre. "Öğrenerek" sütunu, Python'a hâkim ama
Flask/SMTP/paketleme gibi parçaların bir kısmını ilk kez yapan biri için.

| Aşama | Deneyimli | Öğrenerek |
|---|---|---|
| **0. Keşif** — kütüphaneleri deneme, hangi veri nereden geliyor | 4–8 saat | 1–2 gün |
| **1. Veri katmanı** — fiyat, KAP, haber, analist + yedek zincirler | 1–2 gün | 4–6 gün |
| **2. Veritabanı + takip listesi** | 3–5 saat | 1–2 gün |
| **3. E-posta** — HTML şablon, SMTP, Gmail App Password | 1 gün | 2–4 gün |
| **4. Web arayüzü (ilk sürüm)** | 1–2 gün | 3–5 gün |
| **5. Zamanlama + Windows paketleme** | 4–8 saat | 2–3 gün |
| **6. Hata ayıklama** (aşamalara yayılı) | 2–3 gün | 5–10 gün |
| **7. Güvenlik sertleştirme** | 2–4 gün | 1–3 hafta* |
| **8. Koyu tema + grafik + detay sayfası** | 2–4 gün | 1–2 hafta |
| **9. Belgeler** | 4–8 saat | 1–2 gün |

**Toplam:** deneyimli için ~3–4 hafta tam zamanlı eşdeğeri.
Öğrenerek ilerleyen biri için akşamları çalışarak **3–6 ay**.

\* Güvenlik satırındaki yıldız önemli: bu ayrı bir uzmanlık alanı. Öğrenerek
yapılabilir ama "ne aramak gerektiğini bilmek" en zor kısmı. Çoğu kişisel proje
bu adımı hiç yapmaz — ve localhost'ta çalışan bir araç için bu savunulabilir
bir tercih. (Ama CSRF açığı gerçekten sömürülebilirdi; kanıtladık.)

---

## 3. Hangi sırayla ilerlemelisin

Sıra önemli. Yanlış sırada başlarsan çöpe giden iş çıkar.

### Adım 0 — Önce en riskli bilinmeyeni çöz (yarım gün)

**Kod yazmadan önce veriyi alabildiğini kanıtla.** 20 satırlık bir betikle:

```python
import borsapy as bp
t = bp.Ticker("THYAO")
print(t.info)                    # fiyat geliyor mu?
print(t.news)                    # KAP bildirimi geliyor mu?
print(t.analyst_price_targets)   # analist verisi var mı?
```

Bu çalışmıyorsa projenin tamamı çalışmaz. Önce bunu bil.

Bu projede en kritik bilgi buradan çıktı: borsapy istenen dört veri türünden
üçünü tek kütüphaneden veriyor. Bunu bilmeden mimariye başlasan, KAP için ayrı
bir kazıyıcı yazmaya kalkardın — birkaç gün boşa.

### Adım 1 — Veriyi normalize et, sonra üstüne bina kur

Her kaynak farklı biçimde veri döndürüyor. Önce **kendi tiplerini** tanımla
(`PriceData`, `NewsItem`), sonra her kaynağı o tipe çeviren birer fonksiyon yaz.

Bunu atlayıp doğrudan borsapy'nin döndürdüğü DataFrame'i şablona geçirirsen,
yedek kaynak eklemek istediğinde her şeyi baştan yazarsın.

### Adım 2 — Çıktıyı erken gör

Veri geldikten hemen sonra HTML e-posta şablonunu yaz ve `--dry-run` ile
diske yaz. Tarayıcıda aç. **SMTP'yi en sona bırak.**

Sebep: SMTP kurulumu (App Password, 2FA, port ayarları) kendi başına bir
sürtünme kaynağı. Çıktıyı görmek için ona ihtiyacın yok, ve görmeden ilerlemek
körlemesine kod yazmak demek.

### Adım 3 — Hata yolunu baştan tasarla

Bu projedeki en önemli tasarım kararı buydu ve **sonradan eklenemez**:
her veri çekme işlemi ya veri ya hata döndürüyor, hiçbiri programı düşürmüyor.

Sonradan eklemeye kalksan tüm çağrı zincirini değiştirmen gerekir.

### Adım 4 — Arayüz, zamanlama, paketleme

Bunlar sırayla gelebilir. Arayüzü basit tut, çalıştıktan sonra güzelleştir.

### Adım 5 — Güvenlik ve cila en sonda

---

## 4. Zaman aslında nereye gidiyor

İşin şaşırtıcı kısmı bu. Kod yazmak yavaş kısım değil.

```
Kod yazmak            ████████░░░░░░░░░░░░  ~30%
Entegrasyon/uyumsuzluk ██████░░░░░░░░░░░░░░  ~25%
Teşhis (neden çalışmıyor?) ███████░░░░░░░░░  ~30%
Ortam/paketleme sorunları ███░░░░░░░░░░░░░░  ~15%
```

Bu projede çıkan sekiz hataya bak — hiçbiri "algoritma yanlış" değil. Hepsi
**iki şeyin birbirine uymaması**:

| Hata | Ne uymadı | Tek başına ne kadar sürerdi |
|---|---|---|
| Önceki kapanış boş geliyordu | borsapy `prev_close` diyor, dokümantasyonu `close` | 1–3 saat |
| E-posta gönderimi çöküyordu | Yeni e-posta API'sine eski API'nin nesnesi verildi | 1–2 saat |
| PowerShell betiği hiç açılmadı | Dosyada BOM yok, Windows 5.1 yanlış kod sayfasıyla okudu | 2–6 saat |
| Zamanlayıcı başlarken çöktü | `next_run` bir yerde fonksiyon, bir yerde özellik | 30 dk – 2 saat |
| Dönem kutuları karışık sırada | Flask JSON anahtarlarını alfabetik sıralıyor | 30 dk – 1 saat |
| KAP hatası "haber yok" gibi görünüyordu | Yedek kütüphane hata atmak yerine boş döndürüyor | **fark edilmeyebilirdi** |
| Gönderim çökünce haberler kayboldu | İşaretleme gönderimden önce yapılıyordu | **fark edilmeyebilirdi** |
| Yeşil/kırmızı renk körlüğünde ayrılmıyor | — | **aranmasa bulunamazdı** |

Son üçü en pahalı olanlar, çünkü **sessizce yanlış çalışıyorlar**. Program
hata vermiyor, sadece yanlış şey yapıyor. Bunları bulmak için ya şüphelenmen
ya da ölçmen gerekiyor.

---

## 5. Gerçekten zor olan üç şey

### a) Kütüphanenin kaynak kodunu okumak

borsapy'nin dokümantasyonu `close` diyor, kodu `prev_close` üretiyor. Bunu
öğrenmenin tek yolu paketin içine bakmaktı:

```
site-packages/borsapy/_providers/tradingview.py
```

Bu bir beceri ve öğrenilebilir: dokümantasyon yalan söylediğinde kaynağa
git. Türkçe piyasa kütüphaneleri gibi küçük ekiplerin yazdığı paketlerde
dokümantasyonun koddan geri kalması yaygındır.

### b) Sessiz başarısızlığı görmek

"Bugün bildirim yok" ile "KAP'a ulaşamadım" aynı ekranda aynı görünüyor.
Aralarındaki farkı görmek için ya kodu okuman ya da tanı komutu yazman lazım
(`--diagnose-kap` tam bunun için var).

Genel kural: **bir fonksiyon "boş sonuç" döndürüyorsa, bunun "gerçekten boş"
mu yoksa "alamadım" mı olduğunu sor.**

### c) Windows'a paketlemek

Kodun çalışması ile başkasının bilgisayarında çift tıklayınca çalışması
arasında ciddi mesafe var. Bu projede takıldığımız yerler:

- PowerShell'in betik çalıştırma kısıtı
- UTF-8 BOM meselesi
- Sanal ortam yolları (venv taşınınca `pip.exe` bozulur, `python.exe` bozulmaz)
- OneDrive'ın klasörü senkronize etmeye çalışması
- Korumalı klasörde (System32) yazma izni olmaması

Bunların hiçbiri "programlama" değil, ama hepsi gerçek.

---

## 6. Kolay görünüp kolay olan, zor görünüp kolay olan

**Zor sanılıp kolay çıkanlar:**

- *Grafik çizmek.* Kendi SVG çizgi grafiğin ~300 satır, yarım gün. Kütüphane
  kullanırsan 30 dakika. Korkulacak bir şey değil.
- *HTML e-posta.* Satır içi stil + tablo düzeni kuralına uyarsan sorun çıkmıyor.
- *SQLite.* Kurulum yok, ORM'siz 200 satırda biter.
- *Paralel veri çekme.* `ThreadPoolExecutor` ile 10 satır.

**Kolay sanılıp zor çıkanlar:**

- *Gmail'e e-posta göndermek.* Kod 20 satır; App Password + 2FA + şablon
  değerlerin gerçek sanılması yüzünden yarım gün gidebiliyor.
- *"Sadece localhost'ta çalışıyor" güvenliği.* Tarayıcı, senin ziyaret ettiğin
  siteden localhost'a form gönderebiliyor. Bu sezgiye aykırı ve gerçek.
- *Tarih/saat.* "TSİ 18:30" demek, makinenin saat dilimini bilmeyen bir
  zamanlayıcıya çevirmek gerektiriyor.
- *Türkçe karakterler.* Kod sayfası, BOM, `İ`/`ı` dönüşümü, arama katlaması.

---

## 7. Neyi atlayabilirsin

Bu projenin hepsi zorunlu değil. Sadece kendin için çalışan bir sürüm istiyorsan:

| Atlanabilir | Kazanılan süre | Riski |
|---|---|---|
| Güvenlik sertleştirme | 2–4 gün | Localhost'ta düşük; dışarı açarsan yüksek |
| Yedek veri kaynağı (yfinance) | Yarım gün | borsapy düşünce rapor boş gelir |
| Windows başlatıcı + kısayol | 1 gün | Her seferinde terminale girersin |
| Test paketi | 1 gün | Hatalar ancak kullanırken çıkar |
| Grafik ve detay sayfası | 2–4 gün | Sadece e-posta raporun olur |
| Belgeler | 1 gün | 6 ay sonra kendi kodunu anlamazsın |

**Atlanmaması gerekenler:** hata yönetimi felsefesi (sonradan eklenemez),
`.env` ile gizli bilgi ayrımı (baştan yapılmazsa şifre koda sızar), ve
`--dry-run` (olmadan her denemede kendine e-posta yollarsın).

---

## 8. Sıfırdan başlasan, minimum çalışan sürüm

Bir hafta sonunda bitirebileceğin bir çekirdek var:

```
Cumartesi sabah   Keşif: borsapy çalışıyor mu, hangi veriler geliyor
Cumartesi öğleden  Fiyat çekme + SQLite takip listesi + CLI ile ekle/çıkar
Cumartesi akşam    HTML e-posta şablonu + --dry-run ile diske yaz
Pazar sabah        SMTP + Gmail App Password + gerçek gönderim
Pazar öğleden      KAP bildirimleri + haber
Pazar akşam        schedule ile günlük otomatik gönderim
```

Web arayüzü, grafik, güvenlik ve paketleme sonraki hafta sonlarına kalır.
Bu sıra doğru: **her adımın sonunda çalışan bir şeyin oluyor.**

---

## 9. Bu projede senin yaptığın kısım

Dürüst olmak gerekirse: hataların yarısını sen buldun.

- Önceki kapanışın boş olması ve haber özetinin tekrar etmesi — ilk ekran
  görüntünden çıktı
- PowerShell'in çökmesi — senin hata mesajından
- E-posta gönderiminin çökmesi — senin hata mesajından
- Haberlerin kaybolması — senin log'undaki "12 haber → 0 haber" düşüşünden

Ben sandbox'ta ağ erişimi olmadığı için bunların hiçbirini kendi başıma
göremezdim. Gerçek ortamda çalıştırıp çıktıyı geri getirmek, teşhisin yarısı.
Yalnız çalışsan bu döngüyü yine sen kurardın — sadece "bu çıktı yanlış" demek
yerine "neden yanlış" sorusunu da tek başına cevaplaman gerekirdi. Asıl fark
hız: bilinen bir tuzağı tanımak dakikalar, sıfırdan teşhis etmek saatler.

---

## 10. Bundan sonra kendi başına geliştirmek istersen

**Küçük ve güvenli başlangıçlar** (her biri 1–3 saat):

- `.env`'den yeni bir ayar ekle (örn. e-postada gösterilecek haber sayısı)
- E-posta şablonunda renk/düzen değiştir — `templates/email.html`
- Yeni bir alarm türü — `report.py` → `_evaluate_alerts()`

**Orta seviye** (yarım – 1 gün):

- Takip listesine sektör bilgisi ekle (borsapy'de var)
- Detay sayfasına hacim grafiği ekle (ayrı grafik olarak — **asla ikinci
  eksen olarak değil**, o en yaygın grafik hatası)
- Haftalık özet e-postası

**Büyük** (birkaç gün):

- Portföy takibi: adet ve maliyet girip kâr/zarar hesaplama
  (borsapy'nin `Portfolio` sınıfı var)
- Teknik gösterge alarmları (RSI, hareketli ortalama kesişimi)

Her değişiklikten sonra `python app.py --self-test` çalıştır — 90+ kontrol
ağ gerektirmeden geçer ve bir şeyi bozup bozmadığını hemen söyler.

---

*Kod okumak, kod yazmaktan daha çok zaman alır. Bu belge ve `MIMARI.md`
tam olarak onu kısaltmak için var.*
