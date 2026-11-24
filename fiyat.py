"""Ürün sayfasından fiyat ve başlık okuma.

Sayfanın kendi yayınladığı yapılandırılmış veriyi kullanır:
  1) JSON-LD (schema.org Product / Offer)
  2) meta etiketleri (product:price:amount, og:price:amount, itemprop=price)
Bot koruması olan sayfaları atlatmaya çalışmaz; okunamazsa None döner.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from curl_cffi import requests

HEADERS = {"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}
_session = requests.Session(impersonate="chrome")


@dataclass
class Sonuc:
    url: str           # yönlendirmelerden sonraki son adres
    baslik: str | None
    fiyat: float | None
    para: str | None
    stokta: bool | None = None
    hata: str | None = None


def sayi_coz(deger) -> float | None:
    """'1.299,90 TL', '1299.9', 1299 gibi değerleri float'a çevirir."""
    if deger is None:
        return None
    if isinstance(deger, (int, float)):
        return float(deger)
    s = re.sub(r"[^\d.,]", "", str(deger))
    if not s:
        return None
    if "," in s and "." in s:
        # Hangisi sonda ise o ondalık ayırıcıdır
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        bas, _, son = s.rpartition(",")
        s = s.replace(",", ".") if len(son) <= 2 else s.replace(",", "")
    elif s.count(".") == 1 and len(s.rpartition(".")[2]) == 3:
        s = s.replace(".", "")  # 1.299 -> binlik ayırıcı
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _ldjson_urunler(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            veri = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        yigin = [veri]
        while yigin:
            x = yigin.pop()
            if isinstance(x, list):
                yigin.extend(x)
            elif isinstance(x, dict):
                tip = x.get("@type")
                tipler = tip if isinstance(tip, list) else [tip]
                if "Product" in tipler or "ProductGroup" in tipler:
                    yield x
                for k in ("@graph", "mainEntity", "hasVariant"):
                    if k in x:
                        yigin.append(x[k])


def _teklif_fiyati(offers):
    """offers alanından (fiyat, para, stokta) çıkarır; varyantlarda en düşüğü alır."""
    adaylar = []
    yigin = [offers]
    while yigin:
        o = yigin.pop()
        if isinstance(o, list):
            yigin.extend(o)
            continue
        if not isinstance(o, dict):
            continue
        if "offers" in o:
            yigin.append(o["offers"])
        fiyat = sayi_coz(o.get("price") or o.get("lowPrice"))
        if fiyat is None and isinstance(o.get("priceSpecification"), (dict, list)):
            yigin.append(o["priceSpecification"])
            continue
        if fiyat:
            stok = o.get("availability")
            stokta = None if stok is None else ("InStock" in str(stok) or "LimitedAvailability" in str(stok))
            adaylar.append((fiyat, o.get("priceCurrency"), stokta))
    if not adaylar:
        return None
    stoktakiler = [a for a in adaylar if a[2] is not False] or adaylar
    return min(stoktakiler, key=lambda a: a[0])


def _meta(soup: BeautifulSoup, *adlar):
    for ad in adlar:
        t = soup.find("meta", attrs={"property": ad}) or soup.find("meta", attrs={"name": ad}) \
            or soup.find(attrs={"itemprop": ad})
        if t:
            v = t.get("content") or t.get("value") or t.get_text(strip=True)
            if v:
                return v
    return None


def html_coz(html: str, url: str) -> Sonuc:
    soup = BeautifulSoup(html, "html.parser")
    baslik = None
    fiyat = para = stokta = None

    for urun in _ldjson_urunler(soup):
        baslik = baslik or urun.get("name")
        sonuc = _teklif_fiyati(urun.get("offers") or urun.get("hasVariant"))
        if sonuc:
            fiyat, para, stokta = sonuc
            break

    if fiyat is None:
        fiyat = sayi_coz(_meta(soup, "product:price:amount", "og:price:amount", "price"))
        para = para or _meta(soup, "product:price:currency", "og:price:currency", "priceCurrency")

    baslik = baslik or _meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None)
    if baslik:
        baslik = re.sub(r"\s+", " ", baslik).strip()[:120]
    return Sonuc(url=url, baslik=baslik, fiyat=fiyat, para=(para or "TRY").upper(), stokta=stokta)


def _cerezler(url: str) -> dict | None:
    # Yurt dışı sunucudan (GitHub) gelince "ülke seç" sayfasına yönlenmesin
    if "trendyol.com" in url:
        return {"storefrontId": "1", "countryCode": "TR", "language": "tr"}
    return None


def fiyat_oku(url: str) -> Sonuc:
    try:
        r = _session.get(url, headers=HEADERS, timeout=30, allow_redirects=True,
                         cookies=_cerezler(url))
    except Exception as e:  # ağ hatası
        return Sonuc(url, None, None, None, hata=f"bağlantı hatası: {e.__class__.__name__}")
    son_url = str(r.url)
    if "select-country" in son_url:
        son_url = url
    if r.status_code >= 400 or r.status_code == 202:
        return Sonuc(son_url, None, None, None, hata=f"site {r.status_code} döndürdü (muhtemelen bot koruması)")
    sonuc = html_coz(r.text, son_url)
    if sonuc.fiyat is None:
        sonuc.hata = "sayfada fiyat bilgisi yok, site bot koruması kullanıyor olabilir"
    return sonuc


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        print(fiyat_oku(u))
