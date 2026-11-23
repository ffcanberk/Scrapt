"""Koçtaş ürününü Akakçe üzerinden rakip fiyatlarla karşılaştırır.

Kullanım:
    python scraper.py <koctas_url> [<koctas_url> ...]
    python scraper.py --csv products.csv        (CSV'de 'url' sütunu)
    python scraper.py --headed ...              (tarayıcıyı görünür aç)

Çıktı: results/<tarih>_offers.csv  ve  results/<tarih>_summary.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

AKAKCE = "https://www.akakce.com"
DELAY_RANGE = (3.0, 6.0)  # sayfalar arası bekleme (saniye) - siteyi yormamak için
AUTO_ACCEPT_SCORE = 6     # bu skor ve üstü otomatik eşleşme kabul edilir
CACHE_DIR = Path(__file__).parent / "cache"
COLORS = {"siyah", "beyaz", "kirmizi", "mavi", "yesil", "sari", "mor", "pembe", "turuncu",
          "gri", "kahverengi", "lacivert", "bej", "krem", "antrasit", "turkuaz", "taba",
          "bordo", "gumus", "altin", "bakir", "inox", "lila", "fume", "vizon", "haki"}
# Bir ürünün farklı modelini/sürümünü işaret eden kelimeler (Basic vs Digital, montaj dahil/hariç...)
VARIANTS = {"basic", "digital", "dijital", "smart", "plus", "pro", "max", "mini", "lite",
            "premium", "stx", "ultra", "montaj", "hariç", "haric", "inverter", "wifi"}
# Aynı ürün değil, ona ait parça/aksesuar ya da set olduğunu gösteren kelimeler
ACCESSORY = {"uyumlu", "yedek", "pervane", "pervanesi", "aparat", "aparati", "kilif", "kılıf",
             "set", "seti", "takim", "takimi", "kombin", "paketi"}
# İki ilanı karşılaştırırken anlam taşımayan, tanımlayıcı kelimeler (tekrar ilan tespiti için)
NOISE = COLORS | {"ayakli", "uzaktan", "kumandali", "elektrikli", "fanli", "isitici", "portatif",
                  "renkli", "cok", "koctas", "adet", "orijinal", "yeni", "model", "urun", "ve",
                  "ile", "icin", "watt", "beyaz", "siyah"}


# ---------------------------------------------------------------- yardımcılar
def norm(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir: 'Basık' -> 'basik'."""
    text = text.replace("I", "ı").replace("İ", "i").lower()
    text = text.translate(str.maketrans("ıöüşçğ", "iouscg"))
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def parse_price(text: str) -> float | None:
    """'1.906,18 TL' -> 1906.18"""
    m = re.search(r"\d[\d.]*,\d+|\d[\d.]*", text.replace("\xa0", " "))
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def extract_specs(text: str) -> dict:
    """Metinden hacim (lt) ve ölçüleri (cm) çıkarır."""
    t = norm(text).replace("-", " ")
    specs: dict = {}
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:lt|litre|l)\b", t)
    if m and float(m.group(1).replace(",", ".")) <= 1000:   # '2090-L' model kodu, hacim değil
        specs["lt"] = float(m.group(1).replace(",", "."))
    # çoklu paket: "6'li paket", "4 adet", "2'li set" -> kaç adet satıldığı
    m = re.search(r"\b(\d+)\s*'?\s*(?:li|lu)?\s*(?:paket|adet|pcs)\b", t)
    specs["paket"] = int(m.group(1)) if m else 1
    found_colors = {c for c in COLORS if re.search(rf"\b{c}\b", t)}
    if found_colors:
        specs["renk"] = found_colors
    m = re.search(r"(\d+)\s*ml\b", t)
    if m:
        specs["ml"] = int(m.group(1))
    m = re.search(r"(\d+)\s*(?:w|watt)\b", t)
    if m:
        specs["w"] = int(m.group(1))
    m = re.search(r"(\d+)\s*(?:parca|prc|li set|lu set)\b", t)
    if m:
        specs["parca"] = int(m.group(1))
    m = re.search(r"\b(\d+)\s*x\s*(\d+)\b", t)
    if m:
        specs["cm"] = int(m.group(1))      # 40x15 -> çap 40
        specs["h"] = int(m.group(2))       # yükseklik 15
    else:
        m = re.search(r"\b(\d+)\s*cm\b", t)
        if m:
            specs["cm"] = int(m.group(1))
    return specs


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", norm(text))}


def model_codes(text: str, pairs: bool = True) -> set[str]:
    """Model kodu adayları: harf+rakam karışık (lx6331, pf20, gsb16re) ya da 4+ haneli sayı (2006)."""
    t = norm(text)
    # 'lx-6331', 'gsb 16 re' gibi bölünmüş kodları da yakalamak için komşu parçaları birleştir
    tokens = re.findall(r"[a-z0-9]+", t)
    codes = set()
    for i, tok in enumerate(tokens):
        if re.fullmatch(r"(?=.*\d)(?=.*[a-z])[a-z0-9]{3,}", tok) or re.fullmatch(r"\d{4,}", tok):
            codes.add(tok)
        if pairs and i + 1 < len(tokens) and tok not in {"cm", "mm", "lt", "ml", "w", "kg", "gr", "v", "x", "l"}:
            pair = tok + tokens[i + 1]
            if re.fullmatch(r"[a-z]{1,3}\d{2,}", pair):
                codes.add(pair)
    # birim içeren kalıpları (2000w, 150ml, 16lt) ve ölçüleri (40x15, 3x19cm) model kodu sayma
    return {c for c in codes
            if not re.fullmatch(r"\d+(w|ml|lt|l|cm|mm|v|kw|gr|kg|lm|ah)", c)
            and not re.fullmatch(r"\d+x\d+(cm|mm)?", c)}


def strong_codes(text: str, pairs: bool = True) -> set[str]:
    """Ürünü tek başına tanımlayabilecek kodlar: harf+rakam (lx6331, dt4) ya da 5+ haneli sayı (16355)."""
    return {c for c in model_codes(text, pairs) if re.search(r"[a-z]", c) or len(c) >= 5}


def polite_wait():
    time.sleep(random.uniform(*DELAY_RANGE))


# ---------------------------------------------------------------- veri yapıları
@dataclass
class KoctasProduct:
    url: str
    sku: str = ""
    name: str = ""
    brand: str = ""
    price: float | None = None
    gtin: str = ""
    specs: dict = field(default_factory=dict)


@dataclass
class Candidate:
    title: str
    url: str
    min_price: float | None
    score: int = 0
    reasons: list = field(default_factory=list)
    guven: str = ""


@dataclass
class Offer:
    store: str
    seller: str
    title: str
    price: float | None


# ---------------------------------------------------------------- Koçtaş
def fetch_koctas(page: Page, url: str) -> KoctasProduct:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if "Access Denied" in page.title():
        raise RuntimeError("Koçtaş isteği engelledi (Access Denied)")
    p = KoctasProduct(url=url)
    for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "Product":
            continue
        p.sku = str(data.get("sku", ""))
        p.name = data.get("name", "")
        brand = data.get("brand") or {}
        p.brand = brand.get("name", "") if isinstance(brand, dict) else str(brand)
        p.gtin = str(data.get("gtin13") or data.get("gtin") or "")
        offers = data.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if offers.get("price"):
            p.price = float(offers["price"])
        break
    if not p.name:
        raise RuntimeError("Koçtaş sayfasında ürün verisi (JSON-LD) bulunamadı")
    p.specs = extract_specs(p.name)
    return p


# ---------------------------------------------------------------- Akakçe
def build_queries(p: KoctasProduct) -> list[str]:
    """Önce barkod, sonra tam isim, sonra sadeleştirilmiş isim dener."""
    name = re.sub(r"[^\w\s]", " ", p.name)
    name = re.sub(r"\s+", " ", name).strip()
    # marka isimde tekrar ediyorsa (Oms Collection Oms ...) baştaki marka kelimesini kullan
    queries = []
    if p.gtin:
        queries.append(p.gtin)
    queries.append(name)
    no_dims = re.sub(r"\b\d+\s*[xX]\s*\d+\b", "", name).strip()
    if no_dims != name:
        queries.append(no_dims)
    return list(dict.fromkeys(q for q in queries if q))


def search_akakce_cached(page: Page, query: str) -> list[Candidate]:
    """Arama sonuçlarını diske kaydeder; aynı sorgu tekrar sorulursa siteye gitmez.
    Böylece eşleştirme kuralları değiştiğinde sonuçlar yeniden istek atmadan puanlanabilir."""
    CACHE_DIR.mkdir(exist_ok=True)
    key = re.sub(r"[^a-z0-9]+", "_", norm(query)).strip("_")[:120]
    path = CACHE_DIR / f"search_{key}.json"
    if path.exists():
        return [Candidate(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
    if page is None:
        return None
    polite_wait()
    cands = search_akakce(page, query)
    path.write_text(json.dumps([{"title": c.title, "url": c.url, "min_price": c.min_price}
                                for c in cands], ensure_ascii=False), encoding="utf-8")
    return cands


def search_akakce(page: Page, query: str) -> list[Candidate]:
    page.goto(f"{AKAKCE}/arama/?q={quote_plus(query)}", wait_until="domcontentloaded", timeout=45000)
    cards = page.locator("#APL > li[data-pr]")
    out = []
    for i in range(min(cards.count(), 20)):
        card = cards.nth(i)
        link = card.locator("a[href*='fiyati,']").first
        if link.count() == 0:
            continue
        href = link.get_attribute("href") or ""
        title = (link.get_attribute("title") or link.inner_text()).strip()
        price_el = card.locator(".pt_v9, .pt_v8").first
        price = parse_price(price_el.inner_text()) if price_el.count() else None
        out.append(Candidate(title=title, url=AKAKCE + href if href.startswith("/") else href,
                             min_price=price))
    return out


def score_candidate(p: KoctasProduct, c: Candidate) -> Candidate:
    """Kural tabanlı eşleştirme puanı. Ölçü çelişkisi varsa aday elenir."""
    # Akakçe URL'si genelde satıcının orijinal başlığını da içerir (oms-basik-16-lt-40x15)
    slug = c.url.rsplit("/", 1)[-1].split(",")[0].replace("en-ucuz-", "").replace("-fiyati", "")
    cand_text = f"{c.title} {slug.replace('-', ' ')}"
    cs = extract_specs(c.title)
    for k, v in extract_specs(slug.replace("-", " ")).items():
        cs.setdefault(k, v)
    ks = p.specs
    score, reasons = 0, []

    brand_word = norm(p.brand).split()[0] if p.brand else ""
    if brand_word and brand_word in words(cand_text):
        score += 2; reasons.append("marka")
    elif brand_word:
        c.score, c.reasons = -99, ["marka yok"]
        return c

    kw, cw = words(p.name), words(cand_text)

    # aksesuar / set / ikili paket: "+ 38 parça uç", "uyumlu pervane", "2'li set" -> aynı ürün değil
    # 'set' ile 'seti', 'pervane' ile 'pervanesi' aynı kök: Koçtaş'ta biri varsa sorun değil
    def stem(w): return re.sub(r"(si|i|ı)$", "", w) if len(w) >= 4 else w
    k_stems = {stem(w) for w in kw}
    extra_acc = {w for w in cw & ACCESSORY if stem(w) not in k_stems}
    # "+" bir şey eklendiğini gösterir; ama "1800 W + 1200 W" gibi teknik değer ise set değildir
    plus_bundle = re.search(r"\+\s*(?!\d+(?:[.,]\d+)?\s*(?:w|watt|v|kw)\b)", norm(c.title))
    if plus_bundle or extra_acc:
        c.score, c.reasons = -99, ["aksesuar/set: " + ("+" if plus_bundle else "/".join(sorted(extra_acc)))]
        return c

    compact = re.sub(r"[^a-z0-9]", "", norm(cand_text))
    hits = sorted(c for c in model_codes(p.name) if c in compact)
    if hits:
        score += 4; reasons.append("model " + "/".join(hits))
    # Koçtaş'ta ayırt edici bir model kodu var ama adayda yoksa muhtemelen başka model
    strong = strong_codes(p.name)
    if strong and not any(c in compact for c in strong):
        score -= 5; reasons.append("model kodu yok (" + "/".join(sorted(strong)) + ")")

    # varyant kelimesi yalnızca birinde varsa (Basic/Digital, Stx, montaj dahil...) ceza
    var_diff = (kw ^ cw) & VARIANTS
    if var_diff:
        score -= 2 * len(var_diff); reasons.append("varyant farkı " + "/".join(sorted(var_diff)))

    for key, label in (("lt", "hacim"), ("ml", "ml"), ("w", "watt"), ("parca", "parça"),
                       ("cm", "ölçü"), ("h", "yükseklik")):
        if key in ks and key in cs:
            if abs(ks[key] - cs[key]) < 0.01:
                score += 3; reasons.append(f"{label} {ks[key]:g}")
            else:
                c.score, c.reasons = -99, [f"{label} farklı ({ks[key]:g} vs {cs[key]:g})"]
                return c

    # çoklu paket: Koçtaş tekli satıyorsa "6'lı paket" aynı ürün sayılmaz
    if ks.get("paket", 1) != cs.get("paket", 1):
        c.score, c.reasons = -99, [f"paket farklı ({ks.get('paket', 1)} vs {cs.get('paket', 1)})"]
        return c

    # renk: ikisinde de renk yazıyor ve hiç ortak renk yoksa farklı varyant
    if ks.get("renk") and cs.get("renk"):
        if ks["renk"] & cs["renk"]:
            score += 2; reasons.append("renk " + "/".join(sorted(ks["renk"] & cs["renk"])))
        else:
            c.score, c.reasons = -99, [f"renk farklı ({'/'.join(ks['renk'])} vs {'/'.join(cs['renk'])})"]
            return c

    common = (words(p.name) & words(cand_text)) - {brand_word} - COLORS
    score += len(common); reasons += sorted(common)

    # fiyat sağduyu kontrolü: yarısından ucuz / 2 katından pahalı ise şüpheli
    if p.price and c.min_price and not (0.5 <= c.min_price / p.price <= 2):
        score -= 3; reasons.append("fiyat şüpheli")

    # güven seviyesi: raporda neyin elle kontrol edilmesi gerektiğini göstermek için
    missing = kw - cw - NOISE - {brand_word}            # Koçtaş'ta olup adayda olmayan kelimeler
    extra = words(c.title) - kw - NOISE - {brand_word}   # adayda olup Koçtaş'ta olmayan
    if var_diff or (strong and not any(x in compact for x in strong)):
        c.guven = "dusuk"
    elif (len(missing) >= 2 and len(extra) >= 2) or score < 9:
        c.guven = "orta"
    else:
        c.guven = "yuksek"
    c.score, c.reasons = score, reasons
    return c


def fetch_akakce_offers(page: Page, url: str) -> tuple[str, list[Offer]]:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.locator("#PL li").first.wait_for(timeout=15000)
    specs_text = ""
    spec_el = page.locator("text=Ürün Özellikleri").first
    if spec_el.count():
        specs_text = spec_el.locator("xpath=..").inner_text()
    offers = []
    rows = page.locator("#PL > li")
    for i in range(rows.count()):
        row = rows.nth(i)
        price_el = row.locator(".pt_v8").first
        if price_el.count() == 0:
            continue
        store_img = row.locator(".v_v8 img").first
        store = store_img.get_attribute("alt") if store_img.count() else ""
        seller = row.locator(".v_v8").first.inner_text().strip().lstrip("/") if row.locator(".v_v8").count() else ""
        title = row.locator(".pn_v8").first.inner_text().strip() if row.locator(".pn_v8").count() else ""
        # bağımsız web mağazalarında (Nalport.com gibi) logo yok; mağaza = satıcı
        offers.append(Offer(store=store or seller, seller=seller, title=title,
                            price=parse_price(price_el.inner_text())))
    return specs_text, offers


# ---------------------------------------------------------------- ana akış
def process(page: Page, url: str, known: dict | None = None) -> dict:
    """known: CSV'den gelen ürün bilgisi (name/brand/price/sku/gtin). Varsa Koçtaş sayfası açılmaz."""
    print(f"\n=== {url or known.get('name')}")
    if known and known.get("name"):
        p = KoctasProduct(url=url, sku=known.get("sku", ""), name=known["name"],
                          brand=known.get("brand", ""), gtin=known.get("gtin", ""),
                          price=float(known["price"]) if known.get("price") else None)
        p.specs = extract_specs(p.name)
    else:
        p = fetch_koctas(page, url)
    print(f"Koçtaş: {p.name} | marka={p.brand} | fiyat={p.price} | barkod={p.gtin or '-'} | ölçüler={p.specs}")

    result = {"koctas": asdict(p), "match": None, "status": "eslesme_yok", "offers": [],
              "adaylar": "", "ilan_sayisi": 0}
    best: Candidate | None = None
    top: list[Candidate] = []
    for q in build_queries(p):
        found = search_akakce_cached(page, q)
        if found is None:          # offline modda bu sorgu önbellekte yok
            result["status"] = "onbellekte_yok"
            print(f"  Arama '{q}': önbellekte yok")
            return result
        cands = [score_candidate(p, c) for c in found]
        cands.sort(key=lambda c: c.score, reverse=True)
        print(f"  Arama '{q}': {len(cands)} aday")
        for c in cands[:5]:
            print(f"    [{c.score:>3}] {c.title}  ({', '.join(c.reasons)})")
        if cands and cands[0].score >= AUTO_ACCEPT_SCORE:
            top = [c for c in cands if c.score == cands[0].score]
            best = cands[0]
            break

    if not best:
        print("  -> Güvenilir eşleşme bulunamadı, elle kontrol gerekli.")
        return result

    # aynı puanı alanlar aynı ürünün tekrar ilanlarıysa birleştir, değilse elle kontrole bırak
    dups = [c for c in top[1:] if same_listing(best, c, p)]
    if len(dups) < len(top) - 1:
        result.update(status="belirsiz", adaylar=" || ".join(c.title for c in top[:4]))
        print(f"  -> Belirsiz: {len(top)} farklı aday aynı puanda, elle kontrol gerekli.")
        return result

    listings = [best] + dups[:2]   # en fazla 3 ilan
    print(f"  -> Eşleşme: {best.title} (skor {best.score})"
          + (f" + {len(listings) - 1} tekrar ilan birleştirildi" if len(listings) > 1 else ""))
    result.update(match=asdict(best), status="eslesti", ilan_sayisi=len(listings))
    if page is None:               # offline değerlendirme: fiyat sayfaları açılmaz
        return result

    offers, seen, specs_text = [], set(), ""
    for lst in listings:
        polite_wait()
        st, offs = fetch_akakce_offers(page, lst.url)
        specs_text = specs_text or st
        for o in offs:
            key = (norm(o.store), norm(o.seller), o.price)
            if key not in seen:
                seen.add(key); offers.append(asdict(o))
    result.update(offers=offers, akakce_specs=specs_text)
    return result


def same_listing(a: Candidate, b: Candidate, p: KoctasProduct) -> bool:
    """İki Akakçe ilanı aynı ürünün tekrar ilanı mı? (sadece renk/açıklama kelimeleri farklıysa evet)"""
    wa, wb = words(a.title), words(b.title)
    if (wa ^ wb) & VARIANTS:
        return False                                   # Basic vs Digital, montaj dahil vs hariç...
    ca = re.sub(r"[^a-z0-9]", "", norm(a.title + " " + a.url))
    cb = re.sub(r"[^a-z0-9]", "", norm(b.title + " " + b.url))
    # birinin model kodu diğerinde hiç geçmiyorsa farklı model ('PF20' ile 'PF 20' aynı sayılır)
    if any(x not in cb for x in strong_codes(a.title, pairs=False)) or \
       any(x not in ca for x in strong_codes(b.title, pairs=False)):
        return False
    strong = strong_codes(p.name)
    if strong and any(c in ca for c in strong) and any(c in cb for c in strong):
        return True                                    # ikisi de Koçtaş'ın model kodunu taşıyor
    # kod yoksa: farklı kelimeler sadece renk/genel açıklama kelimeleri olmalı
    return not ((wa ^ wb) - NOISE - words(p.name))


def write_reports(results: list[dict]) -> tuple[Path, Path]:
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    offers_path = out_dir / f"{stamp}_offers.csv"
    summary_path = out_dir / f"{stamp}_summary.csv"

    with offers_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["koctas_sku", "koctas_urun", "magaza", "satici", "rakip_baslik", "fiyat"])
        for r in results:
            for o in r["offers"]:
                w.writerow([r["koctas"]["sku"], r["koctas"]["name"], o["store"], o["seller"],
                            o["title"], o["price"]])

    with summary_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["koctas_sku", "koctas_urun", "koctas_fiyat", "koctas_liste_fiyati",
                    "koctas_fiyat_kaynagi", "durum", "guven", "akakce_urun",
                    "eslesme_skoru", "satici_sayisi", "en_ucuz_magaza", "en_ucuz_fiyat",
                    "fark_tl", "fark_yuzde", "akakce_url", "birlesen_ilan", "belirsiz_adaylar"])
        for r in results:
            k = r["koctas"]
            # Koçtaş'ın gerçek satış fiyatı (sepete özel vb.) Akakçe'deki Koçtaş satırında;
            # JSON-LD / CSV'deki fiyat üstü çizili liste fiyatı olabilir.
            own = [o for o in r["offers"] if o["price"] and norm(o["store"]) == "koctas"]
            rivals = [o for o in r["offers"] if o["price"] and norm(o["store"]) != "koctas"]
            if own:
                k_price, source = min(o["price"] for o in own), "akakce"
            else:
                k_price, source = k["price"], "liste/csv"
            cheapest = min(rivals, key=lambda o: o["price"]) if rivals else None
            diff = (k_price - cheapest["price"]) if (cheapest and k_price) else None
            w.writerow([k["sku"], k["name"], k_price, k["price"], source, r["status"],
                        (r["match"] or {}).get("guven", ""),
                        (r["match"] or {}).get("title", ""), (r["match"] or {}).get("score", ""),
                        len(r["offers"]),
                        cheapest["store"] if cheapest else "", cheapest["price"] if cheapest else "",
                        round(diff, 2) if diff is not None else "",
                        round(100 * diff / cheapest["price"], 1) if diff is not None else "",
                        (r["match"] or {}).get("url", ""), r.get("ilan_sayisi", ""),
                        r.get("adaylar", "")])
    write_xlsx(summary_path, offers_path, out_dir / f"{stamp}_rapor.xlsx")
    return offers_path, summary_path


def write_xlsx(summary_path: Path, offers_path: Path, out_path: Path) -> None:
    """CSV raporlarını tek Excel dosyasına çevirir; fiyatlar gerçek sayı olarak yazılır."""
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return
    numeric = {"koctas_fiyat", "koctas_liste_fiyati", "eslesme_skoru", "satici_sayisi",
               "en_ucuz_fiyat", "fark_tl", "fark_yuzde", "fiyat", "birlesen_ilan"}
    money = {"koctas_fiyat", "koctas_liste_fiyati", "en_ucuz_fiyat", "fark_tl", "fiyat"}
    wide = {"koctas_urun", "akakce_urun", "rakip_baslik", "akakce_url", "belirsiz_adaylar"}
    wb = Workbook()
    for i, (path, title) in enumerate(((summary_path, "Ozet"), (offers_path, "Fiyatlar"))):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        with path.open(encoding="utf-8-sig") as f:
            rows = list(csv.reader(f, delimiter=";"))
        header = rows[0]
        ws.append(header)
        for r in rows[1:]:
            ws.append([float(v) if h in numeric and v != "" else v for h, v in zip(header, r)])
        for col, h in enumerate(header, 1):
            letter = get_column_letter(col)
            ws.column_dimensions[letter].width = 45 if h in wide else 14
            if h in money:
                for cell in ws[letter][1:]:
                    cell.number_format = "#,##0.00"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    wb.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--csv", help="'url' sütunu olan CSV dosyası")
    ap.add_argument("--headed", action="store_true", help="tarayıcıyı görünür aç")
    ap.add_argument("--offline", action="store_true",
                    help="siteye gitmeden, önbellekteki aramalarla sadece eşleştirmeyi değerlendir")
    args = ap.parse_args()

    items: list[dict] = [{"url": u} for u in args.urls]
    if args.csv:
        # sütunlar: url, sku, name, brand, price, gtin  (name doluysa Koçtaş sayfası açılmaz)
        with open(args.csv, encoding="utf-8-sig") as f:
            sample = f.read(2048); f.seek(0)
            delim = ";" if sample.count(";") > sample.count(",") else ","
            items += [{k: (v or "").strip() for k, v in row.items()}
                      for row in csv.DictReader(f, delimiter=delim)]
    if not items:
        ap.error("En az bir Koçtaş URL'si ya da --csv verin")

    results = []
    if args.offline:
        from collections import Counter
        for n, item in enumerate(items, 1):
            print(f"\n[{n}/{len(items)}]", end="")
            results.append(process(None, item.get("url", ""), item))
        print("\n\nDURUM:", dict(Counter(r["status"] for r in results)))
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page(locale="tr-TR")
        for n, item in enumerate(items, 1):
            url = item.get("url", "")
            print(f"\n[{n}/{len(items)}]", end="")
            try:
                results.append(process(page, url, item))
            except (RuntimeError, PWTimeout) as e:
                print(f"  !! HATA: {e}")
                results.append({"koctas": asdict(KoctasProduct(url=url, name=item.get("name", ""))),
                                "match": None, "status": f"hata: {e}", "offers": []})
            polite_wait()
        browser.close()

    offers_path, summary_path = write_reports(results)
    print(f"\nRaporlar:\n  {summary_path}\n  {offers_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
