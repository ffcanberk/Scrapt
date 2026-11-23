# Koçtaş – Akakçe Fiyat Karşılaştırma Scraper'ı: Kod Tarifi

Bu belge, `scraper.py` adlı tek dosyalık Python programının sözel tarifidir. Bir yazılım agent'ı bu belgeyi okuyarak aynı davranışı gösteren kodu yazabilmelidir. Tüm eşikler, kurallar ve HTML seçicileri aşağıda birebir verilmiştir.

---

## 1. Amaç

Koçtaş'ta satılan bir ürünün rakip mağazalardaki fiyatlarını bulmak. Rakip fiyatları tek tek mağazalardan değil, fiyat karşılaştırma sitesi **Akakçe** (akakce.com) üzerinden toplanır; Akakçe aynı ürünün tüm satıcılarını tek sayfada listeler ve Koçtaş da çoğu zaman bu listede yer alır (Koçtaş'ın gerçek satış fiyatı da buradan alınır).

Akış: Koçtaş ürün adı → Akakçe'de arama → adayları kurallarla puanla → doğru ürünü seç → o ürünün Akakçe sayfasındaki tüm satıcı fiyatlarını oku → CSV + Excel raporu yaz.

---

## 2. Teknoloji ve bağımlılıklar

- Python 3.11+ (`from __future__ import annotations` kullan).
- `playwright` (sync API, Chromium, headless). Kurulum: `pip install playwright openpyxl` ve `python -m playwright install chromium`.
- `openpyxl` (Excel çıktısı; yoksa Excel adımı sessizce atlanır).
- Standart kütüphane: argparse, csv, json, random, re, sys, time, unicodedata, dataclasses, datetime, pathlib, urllib.parse.quote_plus.

---

## 3. Komut satırı

```
python scraper.py <koctas_url> [<koctas_url> ...]
python scraper.py --csv products.csv
python scraper.py --csv products.csv --offline
python scraper.py --headed ...
```

- `urls` (pozisyonel, sıfır veya daha fazla): Koçtaş ürün URL'leri. Bu durumda ürün bilgisi Koçtaş sayfasından okunur.
- `--csv`: Girdi dosyası. Ayırıcı otomatik algılanır: dosyanın ilk 2048 karakterinde `;` sayısı `,` sayısından fazlaysa `;`, değilse `,`. Kodlama `utf-8-sig`. Sütunlar: `url, sku, name, brand, price, gtin`. Tüm değerler `strip` edilir. **`name` doluysa Koçtaş sayfası hiç açılmaz**, bilgiler CSV'den alınır (`price` boşsa `None`).
- `--headed`: Tarayıcıyı görünür aç (varsayılan headless).
- `--offline`: Tarayıcı açma; sadece önbellekteki arama sonuçlarıyla eşleştirmeyi değerlendir, her ürün için durumu yazdır, sonda `DURUM: {...}` sayımını (collections.Counter) bas ve rapor yazmadan çık.
- Hiç URL ve CSV yoksa argparse hatası: "En az bir Koçtaş URL'si ya da --csv verin".
- `if __name__ == "__main__":` içinde önce `sys.stdout.reconfigure(encoding="utf-8")`, sonra `main()`.

---

## 4. Sabitler

- `AKAKCE = "https://www.akakce.com"`
- `DELAY_RANGE = (3.0, 6.0)`: her siteye gidişten önce `random.uniform` ile bu aralıkta saniye beklenir (`polite_wait()`).
- `AUTO_ACCEPT_SCORE = 6`: en iyi adayın puanı bu değer veya üstündeyse eşleşme kabul edilir.
- `CACHE_DIR = <script klasörü>/cache`
- `COLORS` = {siyah, beyaz, kirmizi, mavi, yesil, sari, mor, pembe, turuncu, gri, kahverengi, lacivert, bej, krem, antrasit, turkuaz, taba, bordo, gumus, altin, bakir, inox, lila, fume, vizon, haki}
- `VARIANTS` (farklı model/sürüm belirten kelimeler) = {basic, digital, dijital, smart, plus, pro, max, mini, lite, premium, stx, ultra, montaj, hariç, haric, inverter, wifi}
- `ACCESSORY` (aksesuar/set belirten kelimeler) = {uyumlu, yedek, pervane, pervanesi, aparat, aparati, kilif, kılıf, set, seti, takim, takimi, kombin, paketi}
- `NOISE` (tekrar ilan tespitinde önemsiz kelimeler) = COLORS ∪ {ayakli, uzaktan, kumandali, elektrikli, fanli, isitici, portatif, renkli, cok, koctas, adet, orijinal, yeni, model, urun, ve, ile, icin, watt, beyaz, siyah}

Tüm kelime karşılaştırmaları `norm()` uygulanmış (ASCII'ye indirilmiş) metin üzerinde yapılır; bu yüzden sabitler ASCII yazılır.

---

## 5. Veri yapıları (dataclass)

- `KoctasProduct`: url, sku="", name="", brand="", price: float|None=None, gtin="", specs: dict (boş).
- `Candidate` (Akakçe arama sonucu): title, url, min_price: float|None, score: int=0, reasons: list (boş), guven: str="".
- `Offer` (Akakçe satıcı satırı): store, seller, title, price: float|None.

---

## 6. Yardımcı fonksiyonlar

### 6.1 `norm(text)`
1. Önce `"I"` → `"ı"` ve `"İ"` → `"i"` değiştir (Türkçe büyük/küçük harf), sonra `.lower()`.
2. `ı ö ü ş ç ğ` → `i o u s c g` (str.translate).
3. `unicodedata.normalize("NFKD")` ve tüm combining karakterleri sil.

Örnek: "Basık Tencere" → "basik tencere".

### 6.2 `parse_price(text)`
`\xa0` boşluğa çevrilir. Regex `\d[\d.]*,\d+|\d[\d.]*` ile ilk sayı bulunur; noktalar silinir, virgül noktaya çevrilir, float döner. Bulunamazsa None. ("1.906,18 TL" → 1906.18)

### 6.3 `words(text)`
`norm(text)` içinden en az 3 harfli `[a-z]{3,}` kelimelerin kümesi.

### 6.4 `extract_specs(text)` → dict
Metin `norm` edilir ve `-` boşluğa çevrilir. Sırasıyla:
- **lt**: `(\d+(?:[.,]\d+)?)\s*(?:lt|litre|l)\b` ilk eşleşme; değer ≤ 1000 ise float olarak (virgül nokta yapılır). (1000 üstü, "2090-L" gibi model kodudur.)
- **paket**: `\b(\d+)\s*'?\s*(?:li|lu)?\s*(?:paket|adet|pcs)\b` varsa o sayı, yoksa **1** (her zaman yazılır).
- **renk**: COLORS içinden `\b<renk>\b` ile geçenlerin kümesi (boşsa anahtar konmaz).
- **ml**: `(\d+)\s*ml\b`
- **w**: `(\d+)\s*(?:w|watt)\b`
- **parca**: `(\d+)\s*(?:parca|prc|li set|lu set)\b`
- **ölçü**: `\b(\d+)\s*x\s*(\d+)\b` varsa `cm` = ilk sayı (çap), `h` = ikinci sayı (yükseklik); yoksa `\b(\d+)\s*cm\b` ile `cm`.

### 6.5 `model_codes(text, pairs=True)` → set
`norm(text)` içinden `[a-z0-9]+` token'ları alınır. Her token için:
- Hem harf hem rakam içeren ve en az 3 karakterli token (`(?=.*\d)(?=.*[a-z])[a-z0-9]{3,}`) **veya** en az 4 haneli sayı (`\d{4,}`) → kod.
- `pairs=True` ise ve token bir birim değilse ({cm, mm, lt, ml, w, kg, gr, v, x, l}), token ile bir sonraki token birleştirilir; birleşim `[a-z]{1,3}\d{2,}` kalıbına tam uyuyorsa kod sayılır ("pf"+"20" → "pf20", "lx"+"6331" → "lx6331").
- Sonuçtan çıkarılır: `\d+(w|ml|lt|l|cm|mm|v|kw|gr|kg|lm|ah)` (2000w, 150ml gibi birimler) ve `\d+x\d+(cm|mm)?` (40x15, 3x19cm gibi ölçüler).

### 6.6 `strong_codes(text, pairs=True)`
`model_codes` içinden harf içerenler veya en az 5 haneli sayılar (ürünü tek başına tanımlayabilecek kodlar: lx6331, dt4, 16355).

---

## 7. Koçtaş ürün bilgisini okuma (`fetch_koctas`)

Sadece CSV'de `name` yoksa kullanılır. Not: Koçtaş Akamai bot korumalıdır; headless tarayıcıda genellikle engellenir. Bu nedenle pratikte ürün bilgisi CSV'den verilir.

- `page.goto(url, wait_until="domcontentloaded", timeout=45000)`.
- Sayfa başlığında "Access Denied" varsa `RuntimeError("Koçtaş isteği engelledi (Access Denied)")`.
- Tüm `script[type="application/ld+json"]` blokları JSON olarak okunur; `@type == "Product"` olan ilk blokta: `sku`, `name`, `brand.name` (brand dict değilse str), `gtin13` veya `gtin`, `offers` (liste ise ilk eleman) içindeki `price` → float.
- İsim bulunamazsa `RuntimeError("Koçtaş sayfasında ürün verisi (JSON-LD) bulunamadı")`.
- `specs = extract_specs(name)`.
- Önemli: JSON-LD'deki fiyat çoğu zaman üstü çizili **liste fiyatıdır**; gerçek satış fiyatı ("sepete özel fiyat") Akakçe'deki Koçtaş satırından alınır (bkz. Bölüm 12).

---

## 8. Akakçe araması

### 8.1 Arama sorguları (`build_queries`)
Ürün adındaki noktalama `[^\w\s]` boşluğa çevrilir, çoklu boşluklar teke indirilir. Sorgular sırasıyla:
1. gtin (varsa),
2. temizlenmiş tam ad,
3. adın `\b\d+\s*[xX]\s*\d+\b` ölçüleri silinmiş hali (farklıysa).

Tekrarlar atılır, sıra korunur. Sorgular bu sırayla denenir; bir sorguda kabul edilen eşleşme bulunursa sonrakiler denenmez.

### 8.2 Arama sayfası okuma (`search_akakce`)
- URL: `https://www.akakce.com/arama/?q=<quote_plus(sorgu)>`, `wait_until="domcontentloaded"`, timeout 45000.
- Ürün kartları: `#APL > li[data-pr]`; en fazla ilk 20 kart.
- Her kartta link: `a[href*='fiyati,']` (yoksa kartı atla). Başlık: linkin `title` özniteliği, yoksa `inner_text`. Href `/` ile başlıyorsa başına AKAKCE eklenir.
- En ucuz fiyat: kart içinde `.pt_v9, .pt_v8` ilk elemanın metni → `parse_price`.
- `Candidate(title, url, min_price)` listesi döner.

### 8.3 Önbellek (`search_akakce_cached`)
- Anahtar: `norm(sorgu)` içinde `[^a-z0-9]+` → `_`, baş/son `_` silinir, ilk 120 karakter. Dosya: `cache/search_<anahtar>.json`.
- Dosya varsa oradan `Candidate` listesi döner (siteye gidilmez).
- Yoksa ve `page is None` (offline mod) ise **None** döner.
- Yoksa: `polite_wait()`, `search_akakce` çağrılır, sonuç `[{"title","url","min_price"}]` olarak `ensure_ascii=False` ile kaydedilir.

---

## 9. Eşleştirme puanı (`score_candidate(p, c)`)

Reddedilen aday `score = -99` alır ve `reasons` içine sebebi yazılır. Adımlar bu sırayla:

1. **Aday metni**: Akakçe URL'sinin son parçası alınır, `,` öncesi tutulur, `en-ucuz-` ve `-fiyati` silinir (slug; satıcının orijinal başlığını içerir, ör. `oms-basik-16-lt-40x15`). `cand_text = title + " " + slug('-' yerine boşluk)`.
2. **Aday özellikleri**: `cs = extract_specs(title)`, sonra slug'ın özellikleri sadece eksik anahtarlara eklenir (setdefault). Koçtaş özellikleri `ks = p.specs`.
3. **Marka**: `brand_word = norm(brand)` içindeki ilk kelime. `words(cand_text)` içinde varsa +2 ("marka"); yoksa **reddet** ("marka yok"). Marka boşsa bu adım atlanır.
4. `kw = words(koçtaş adı)`, `cw = words(cand_text)`.
5. **Aksesuar / set reddi**:
   - `stem(w)`: uzunluk ≥ 4 ise sondaki `si|i|ı` silinir (set/seti, pervane/pervanesi aynı kök sayılsın).
   - `extra_acc` = cw ∩ ACCESSORY içinden kökü Koçtaş kelimelerinin kökleri arasında olmayanlar.
   - `plus_bundle`: `norm(title)` içinde `\+\s*(?!\d+(?:[.,]\d+)?\s*(?:w|watt|v|kw)\b)` var mı ("+ 38 parça uç" set demektir; "1800 W + 1200 W" teknik değer olduğu için değildir).
   - Biri varsa **reddet** ("aksesuar/set: ...").
6. **Model kodu**: `compact = norm(cand_text)` içinden `[^a-z0-9]` silinmiş hali. Koçtaş adının `model_codes`'larından `compact` içinde geçenler varsa +4 ("model ...").
7. **Model kodu eksik cezası**: `strong = strong_codes(koçtaş adı)`; strong boş değilse ve hiçbiri `compact` içinde yoksa −5 ("model kodu yok").
8. **Varyant farkı**: `var_diff = (kw △ cw) ∩ VARIANTS` (simetrik fark). Her biri için −2 ("varyant farkı ...").
9. **Sayısal özellikler**: sırasıyla `lt (hacim), ml, w (watt), parca (parça), cm (ölçü), h (yükseklik)`. İki tarafta da varsa: fark < 0.01 ise +3, değilse **reddet** ("<etiket> farklı (a vs b)").
10. **Paket**: `ks.paket` (varsayılan 1) ≠ `cs.paket` (varsayılan 1) ise **reddet** ("6'lı paket" tekli ürün değildir).
11. **Renk**: iki tarafta da renk varsa: kesişim varsa +2, yoksa **reddet**.
12. **Ortak kelimeler**: `(words(koçtaş adı) ∩ words(cand_text)) − {brand_word} − COLORS` → her biri +1.
13. **Fiyat sağduyusu**: Koçtaş fiyatı ve adayın min_price'ı varsa ve oran 0.5–2 aralığı dışındaysa −3 ("fiyat şüpheli").
14. **Güven seviyesi** (`guven`):
    - `missing = kw − cw − NOISE − {brand_word}`
    - `extra = words(title) − kw − NOISE − {brand_word}`
    - var_diff varsa veya model kodu eksikse → `"dusuk"`
    - değilse, (len(missing) ≥ 2 ve len(extra) ≥ 2) veya score < 9 → `"orta"`
    - değilse `"yuksek"`

---

## 10. Tekrar ilan tespiti (`same_listing(a, b, p)`)

Akakçe'de aynı ürün bazen birden fazla ilanla listelenir. İki aday aşağıdaki kurallarla "aynı ürün" sayılır:

1. `words(a.title) △ words(b.title)` içinde VARIANTS kelimesi varsa → **farklı**.
2. `ca`, `cb` = norm(title + " " + url) içinden `[^a-z0-9]` silinmiş metin. a'nın `strong_codes(title, pairs=False)` kodlarından biri `cb` içinde geçmiyorsa ya da b'nin kodlarından biri `ca` içinde geçmiyorsa → **farklı**. ("PF20" ile "PF 20" aynı sayılır.)
3. Koçtaş adının `strong_codes`'u boş değilse ve ikisi de bu kodlardan en az birini içeriyorsa → **aynı**.
4. Değilse: `(words(a) △ words(b)) − NOISE − words(koçtaş adı)` boşsa → **aynı**, değilse **farklı**.

---

## 11. Ana işlem (`process(page, url, known)`)

1. `known` içinde `name` varsa ürünü CSV'den kur (`specs = extract_specs(name)`), yoksa `fetch_koctas`.
2. Sonuç sözlüğü: `koctas` (asdict), `match=None`, `status="eslesme_yok"`, `offers=[]`, `adaylar=""`, `ilan_sayisi=0`.
3. Her sorgu için `search_akakce_cached`. None dönerse (offline + önbellek yok) `status="onbellekte_yok"` ile çık. Adaylar puanlanıp puana göre azalan sıralanır; ilk 5'i log'a yazılır (`[puan] başlık (sebepler)`). En iyi puan ≥ 6 ise: `top` = en iyi puana eşit tüm adaylar, `best` = ilki; döngüden çık.
4. `best` yoksa: `eslesme_yok`.
5. `dups` = `top[1:]` içinden `same_listing(best, c, p)` olanlar. Eşit puanlı adaylardan **herhangi biri** tekrar ilan değilse → `status="belirsiz"`, `adaylar` = ilk 4 adayın başlığı `" || "` ile birleştirilmiş; çık.
6. `listings = [best] + dups[:2]` (en fazla 3 ilan). `status="eslesti"`, `match=asdict(best)`, `ilan_sayisi=len(listings)`.
7. `page is None` (offline) ise burada çık.
8. Her ilan için `polite_wait()` ve `fetch_akakce_offers`. Satıcı satırları `(norm(store), norm(seller), price)` anahtarıyla tekilleştirilerek birleştirilir. `offers` ve `akakce_specs` (ilk dolu özellik metni) kaydedilir.

Log satırları: `=== <url>`, `Koçtaş: <ad> | marka=... | fiyat=... | barkod=... | ölçüler=...`, `Arama '<q>': N aday`, `-> Eşleşme: ... (skor X) [+ N tekrar ilan birleştirildi]`, `-> Belirsiz: ...`, `-> Güvenilir eşleşme bulunamadı, elle kontrol gerekli.`

---

## 12. Akakçe ürün sayfasından fiyatlar (`fetch_akakce_offers`)

- `page.goto(url, domcontentloaded, 45000)`, sonra `#PL li` ilk elemanı için `wait_for(timeout=15000)`.
- Özellik metni: `text=Ürün Özellikleri` ilk elemanının ebeveyninin (`xpath=..`) `inner_text`'i (varsa).
- Satıcı satırları: `#PL > li`. Her satırda:
  - fiyat: `.pt_v8` (yoksa satırı atla) → `parse_price`,
  - mağaza: `.v_v8 img` öğesinin `alt` özniteliği (Hepsiburada, n11, Koçtaş...),
  - satıcı: `.v_v8` metni, baştaki `/` silinmiş,
  - başlık: `.pn_v8` metni,
  - mağaza boşsa (Nalport.com gibi bağımsız web mağazaları logo göstermez) mağaza = satıcı.

---

## 13. Raporlar (`write_reports`)

Klasör: `<script klasörü>/results`, zaman damgası `YYYYMMDD_HHMM`. Tüm CSV'ler `utf-8-sig` kodlamalı ve `;` ayırıcılı.

### 13.1 `<damga>_offers.csv`
Sütunlar: `koctas_sku; koctas_urun; magaza; satici; rakip_baslik; fiyat`. Her ürünün her satıcı satırı ayrı bir satırdır.

### 13.2 `<damga>_summary.csv` (ürün başına 1 satır)
Sütunlar: `koctas_sku; koctas_urun; koctas_fiyat; koctas_liste_fiyati; koctas_fiyat_kaynagi; durum; guven; akakce_urun; eslesme_skoru; satici_sayisi; en_ucuz_magaza; en_ucuz_fiyat; fark_tl; fark_yuzde; akakce_url; birlesen_ilan; belirsiz_adaylar`

Hesap:
- `own` = mağazası `norm(store) == "koctas"` olan satırlar; `rivals` = diğerleri (fiyatı olanlar).
- own varsa `koctas_fiyat = min(own fiyatları)`, kaynak `"akakce"`; yoksa Koçtaş/CSV fiyatı, kaynak `"liste/csv"`.
- `koctas_liste_fiyati` = Koçtaş/CSV'den gelen fiyat.
- `en_ucuz` = rivals içinde en düşük fiyatlı satır.
- `fark_tl = koctas_fiyat − en_ucuz_fiyat` (2 hane yuvarlanmış); `fark_yuzde = 100 × fark / en_ucuz_fiyat` (1 hane). Pozitif değer Koçtaş'ın daha pahalı olduğu anlamına gelir.
- `satici_sayisi` = toplam satır sayısı (Koçtaş dahil). `durum`, `guven`, `akakce_urun` (match.title), `eslesme_skoru`, `akakce_url`, `birlesen_ilan` (ilan_sayisi), `belirsiz_adaylar`.

### 13.3 `<damga>_rapor.xlsx` (`write_xlsx`)
openpyxl ile iki sayfa: `Ozet` (summary CSV'si) ve `Fiyatlar` (offers CSV'si). Sayısal sütunlar (`koctas_fiyat, koctas_liste_fiyati, eslesme_skoru, satici_sayisi, en_ucuz_fiyat, fark_tl, fark_yuzde, fiyat, birlesen_ilan`) boş değilse float olarak yazılır. Para sütunlarına (`koctas_fiyat, koctas_liste_fiyati, en_ucuz_fiyat, fark_tl, fiyat`) `#,##0.00` biçimi uygulanır. Geniş sütunlar (`koctas_urun, akakce_urun, rakip_baslik, akakce_url, belirsiz_adaylar`) 45, diğerleri 14 genişliğinde. İlk satır dondurulur (`A2`) ve otomatik filtre eklenir.

---

## 14. Ana akış (`main`)

- Offline modda Bölüm 3'teki gibi çalışılır.
- Normal mod: `sync_playwright()` → `chromium.launch(headless=not headed)` → `new_page(locale="tr-TR")`.
- Her ürün için `[n/N]` yazılır, `process` çağrılır. `RuntimeError` veya Playwright `TimeoutError` olursa `!! HATA: ...` yazılır, `status="hata: <mesaj>"` ile boş sonuç eklenir ve sonraki ürüne geçilir. Her üründen sonra `polite_wait()`.
- Sonunda tarayıcı kapatılır, `write_reports` çağrılır ve rapor yolları yazdırılır.

---

## 15. Kabul testleri (kod doğru yazıldıysa bu sonuçları vermeli)

| Koçtaş ürünü | Aday | Beklenen |
|---|---|---|
| Oms Basık Tencere 16 Lt. 40x15 (marka Oms Collection) | OMS 2006 40 cm Basık Çelik Tencere (url slug: oms-basik-16-lt-40x15) | kabul; hacim 16, ölçü 40, yükseklik 15 eşleşir |
| aynı | OMS 2008 16 cm Mini Basık Tencere | red: ölçü farklı (40 vs 16) |
| Papilla Fred 20 Cm Tencere Gri Kırçıllı | ... Gri Kırçıllı Granit Tencere - 6'li Paket | red: paket farklı (1 vs 6) |
| Lava ... Çap 24 Cm Kırmızı | Lava Premium Majolik Mor 24 cm ... | red: renk farklı |
| Kumtel 2000 W ... Bej LX-6331 | Kumtel LX-6331 1800 W Fanlı Isıtıcı | red: watt farklı |
| OMS Paslanmaz Çelik Sığ Tencere 26 Cm 2090-l | OMS 2090-L 26 cm Çelik Tencere | kabul, güven yuksek (2090 hacim sayılmaz, "cm2090" kod sayılmaz) |
| Tefal ... Tencere Seti 12 Parça | Tefal ... 12 Parça Büyük Tava Tencere Seti | kabul (set/seti aynı kök) |
| Bosch Gsb 16 Re Darbeli Matkap | Bosch Gsb 16 Re ... + 54 Parça Matkap Ucu Seti | red: aksesuar/set "+" |
| Demirdöküm ... 1800 W + 1200 W ... | — | "+" teknik değer olduğu için set sayılmaz |
| Arçelik 16355 Av Vantilatör | üç ilan: "16355 AV Ayaklı Vantilatör", "16355 Av 50 W Vantilatör - Beyaz", "16355 Av Ayaklı Vantilatör 50 W 40 Cm" | aynı puan; tekrar ilan sayılıp birleştirilir |
| Demirdöküm Termosifon DT4 50 Litre | "DT4 Titanium Basic ... 50 lt" ve "DT4 Titanium Digital ... 50 lt" | belirsiz (Basic/Digital varyant farkı) |

`norm("Basık")` → `"basik"`; `parse_price("1.906,18 TL")` → `1906.18`.
