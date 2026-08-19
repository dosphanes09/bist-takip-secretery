"""
Hisse kodu ↔ şirket adı çözümlemesi ve BIST 100 listesi.

Şirket adı iki yerde işe yarar:
  - Google News sorgusunu daraltmak ("THYAO" OR "Türk Hava Yolları")
  - E-postada ve arayüzde okunabilir başlık göstermek

Öncelik: borsapy'nin canlı KAP şirket listesi → aşağıdaki statik yedek harita
→ hiçbiri yoksa kodun kendisi. Ağ yoksa uygulama yine de çalışır.
"""

from __future__ import annotations

import threading

from logging_setup import get_logger

log = get_logger(__name__)

# Ağ erişimi olmadığında kullanılan yedek harita.
# borsapy erişilebiliyorsa canlı KAP listesi bunu ezer, dolayısıyla bu
# listenin eksik olması sorun değildir — istersen elle genişletebilirsin.
FALLBACK_NAMES: dict[str, str] = {
    "AEFES": "Anadolu Efes",
    "AKBNK": "Akbank",
    "AKSA": "Aksa Akrilik",
    "AKSEN": "Aksa Enerji",
    "ALARK": "Alarko Holding",
    "ARCLK": "Arçelik",
    "ASELS": "Aselsan",
    "ASTOR": "Astor Enerji",
    "BIMAS": "BİM Mağazalar",
    "BRSAN": "Borusan Mannesmann",
    "CCOLA": "Coca-Cola İçecek",
    "CIMSA": "Çimsa",
    "DOAS": "Doğuş Otomotiv",
    "DOHOL": "Doğan Holding",
    "EGEEN": "Ege Endüstri",
    "EKGYO": "Emlak Konut GYO",
    "ENKAI": "Enka İnşaat",
    "EREGL": "Ereğli Demir Çelik",
    "FROTO": "Ford Otosan",
    "GARAN": "Garanti BBVA",
    "GUBRF": "Gübre Fabrikaları",
    "HALKB": "Halkbank",
    "HEKTS": "Hektaş",
    "ISCTR": "İş Bankası",
    "KCHOL": "Koç Holding",
    "KONTR": "Kontrolmatik",
    "KOZAA": "Koza Madencilik",
    "KOZAL": "Koza Altın",
    "KRDMD": "Kardemir",
    "MGROS": "Migros",
    "ODAS": "Odaş Elektrik",
    "OYAKC": "Oyak Çimento",
    "PETKM": "Petkim",
    "PGSUS": "Pegasus",
    "SAHOL": "Sabancı Holding",
    "SASA": "Sasa Polyester",
    "SISE": "Şişecam",
    "SOKM": "Şok Marketler",
    "TAVHL": "TAV Havalimanları",
    "TCELL": "Turkcell",
    "THYAO": "Türk Hava Yolları",
    "TOASO": "Tofaş",
    "TSKB": "TSKB",
    "TTKOM": "Türk Telekom",
    "TUPRS": "Tüpraş",
    "TURSG": "Türkiye Sigorta",
    "ULKER": "Ülker Bisküvi",
    "VAKBN": "VakıfBank",
    "VESTL": "Vestel",
    "YKBNK": "Yapı Kredi",
    "ZOREN": "Zorlu Enerji",
}

_lock = threading.Lock()
_live_names: dict[str, str] | None = None
_live_failed = False


def _load_live_names() -> dict[str, str]:
    """
    borsapy üzerinden tüm BIST şirket listesini bir kez indirir.
    borsapy kendi içinde 24 saat önbellekler; biz de süreç içinde tutarız.
    """
    global _live_names, _live_failed

    with _lock:
        if _live_names is not None:
            return _live_names
        if _live_failed:
            return {}

    try:
        import borsapy as bp

        df = bp.companies()
        mapping = {
            str(row["ticker"]).strip().upper(): str(row["name"]).strip()
            for _, row in df.iterrows()
            if row.get("ticker") and row.get("name")
        }
        with _lock:
            _live_names = mapping
        log.info("KAP şirket listesi yüklendi: %d şirket", len(mapping))
        return mapping
    except Exception as exc:
        log.warning("Şirket listesi alınamadı, statik yedek kullanılacak: %s", exc)
        with _lock:
            _live_failed = True
        return {}


def resolve_name(symbol: str) -> str | None:
    """
    Hisse kodundan şirket adını bulur. Bulunamazsa None döner.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return None

    live = _load_live_names()
    if sym in live:
        return live[sym]

    return FALLBACK_NAMES.get(sym)


def is_valid_symbol(symbol: str) -> tuple[bool, str]:
    """
    Kodun BIST'te gerçekten var olup olmadığını kontrol eder (D-2).

    Bu, `db.validate_symbol_format()` biçim kontrolünün üzerine gelen ikinci
    katmandır. Biçim kontrolü `<script>` → `SCRIPT` gibi zararsızlaştırılmış
    ama anlamsız kodları geçirir; bu kontrol onları da eler.

    Ağ erişimi yoksa **engellemez** — kullanıcının geçerli bir hisseyi
    ekleyememesi, anlamsız bir kaydın listeye girmesinden daha kötüdür.
    Statik yedek harita da kontrole dahil edilir.

    Returns:
        (geçerli_mi, açıklama)
          - (True,  "")            → canlı listede doğrulandı
          - (True,  "doğrulanamadı") → liste alınamadı, biçim kontrolüne güvenildi
          - (False, gerekçe)        → listede yok
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return False, "boş kod"

    live = _load_live_names()
    if not live:
        # Canlı liste yoksa statik yedeğe bak; orada da yoksa engellemeden geç.
        if sym in FALLBACK_NAMES:
            return True, ""
        return True, "doğrulanamadı"

    if sym in live:
        return True, ""
    return False, "BIST'te böyle bir hisse kodu bulunamadı"


def _fold(text: str) -> str:
    """
    Aramada karşılaştırma için metni sadeleştirir.

    Türkçe karakterleri ASCII karşılığına indirger ki "türk" yazınca "TÜRK"
    de, "TURK" de bulunsun. Ayrıca noktalama ve A.Ş./A.O. gibi ekler atılır.
    """
    lowered = (text or "").casefold()
    table = str.maketrans({
        "ı": "i", "İ": "i", "i": "i",
        "ş": "s", "Ş": "s",
        "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o",
        "ç": "c", "Ç": "c",
        "â": "a", "î": "i", "û": "u",
    })
    folded = lowered.translate(table)
    return "".join(ch for ch in folded if ch.isalnum() or ch == " ")


def all_companies() -> list[dict[str, str]]:
    """
    Tüm BIST şirketlerini [{ticker, name}] listesi olarak döndürür.

    Canlı KAP listesi alınabiliyorsa o kullanılır (~600 şirket); ağ yoksa
    statik yedek haritaya düşülür, böylece arama kutusu her hâlükârda çalışır.
    """
    live = _load_live_names()
    source = live if live else FALLBACK_NAMES
    return [{"ticker": t, "name": n} for t, n in sorted(source.items())]


def search_companies(query: str, limit: int = 60) -> list[dict[str, str]]:
    """
    Kod veya şirket adına göre arama yapar.

    Sıralama alaka düzeyine göre:
      1. Kodun tamamı eşleşiyor      (THYAO → THYAO)
      2. Kod sorguyla başlıyor        (THY   → THYAO)
      3. Şirket adı sorguyla başlıyor (türk  → TÜRK HAVA YOLLARI)
      4. Şirket adının içinde geçiyor (hava  → TÜRK HAVA YOLLARI)

    Sorgu boşsa tüm liste (limit kadar) döner — kullanıcı arama yapmadan da
    göz atabilsin diye.
    """
    companies = all_companies()
    needle = _fold(query).strip()

    if not needle:
        return companies[:limit]

    scored: list[tuple[int, str, dict[str, str]]] = []
    for company in companies:
        ticker_folded = _fold(company["ticker"])
        name_folded = _fold(company["name"])

        if ticker_folded == needle:
            rank = 0
        elif ticker_folded.startswith(needle):
            rank = 1
        elif name_folded.startswith(needle):
            rank = 2
        elif needle in name_folded:
            rank = 3
        elif needle in ticker_folded:
            rank = 4
        else:
            continue

        scored.append((rank, company["ticker"], company))

    scored.sort(key=lambda item: (item[0], item[1]))
    return [company for _, _, company in scored[:limit]]


def bist100_symbols() -> list[str]:
    """
    BIST 100 endeksinin güncel bileşenlerini döndürür.
    Alınamazsa statik yedek haritanın anahtarlarını döndürür.
    """
    try:
        import borsapy as bp

        components = bp.Index("XU100").components
        if components is None:
            raise ValueError("bileşen listesi boş")

        # DataFrame ya da liste dönebilir; ikisini de destekle.
        if hasattr(components, "columns"):
            for col in ("ticker", "symbol", "Symbol", "Ticker"):
                if col in components.columns:
                    return sorted({str(v).strip().upper() for v in components[col] if v})
            return sorted({str(v).strip().upper() for v in components.iloc[:, 0] if v})

        return sorted({str(v).strip().upper() for v in components if v})
    except Exception as exc:
        log.warning("BIST 100 listesi alınamadı, yedek liste kullanılıyor: %s", exc)
        return sorted(FALLBACK_NAMES.keys())
