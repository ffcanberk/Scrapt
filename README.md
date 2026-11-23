# Scrapt: Koçtaş – Akakçe Fiyat Karşılaştırma

Koçtaş'taki ürünlerin rakip mağazalardaki fiyatlarını Akakçe üzerinden bulur ve Excel/CSV raporu üretir.

## Kurulum

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
```

## Kullanım

```powershell
# Ürün listesiyle (önerilen)
.venv\Scripts\python scraper.py --csv products_100.csv

# Sitelere gitmeden, önbellekteki aramalarla sadece eşleştirmeyi değerlendir
.venv\Scripts\python scraper.py --csv products_100.csv --offline
```

Girdi CSV sütunları: `url;sku;name;brand;price;gtin`. `name` doluysa Koçtaş sayfası açılmaz (Koçtaş bot korumalı olduğu için önerilen kullanım budur).

## Çıktılar (`results/`)

- `<tarih>_rapor.xlsx`: "Ozet" (ürün başına Koçtaş fiyatı, en ucuz rakip, fark, güven) ve "Fiyatlar" (tüm satıcılar) sayfaları
- `<tarih>_summary.csv`, `<tarih>_offers.csv`: aynı verilerin CSV hali

## Dosyalar

- `scraper.py`: kodun tamamı
- `KOD_TARIFI.md`: kodun ayrıntılı sözel tarifi (başka bir geliştirici/agent aynı kodu bu tariften yazabilir)
- `products_100.csv`: 10 kategoriden 100 ürünlük test listesi
