"""Fiyat takip botu (ntfy veya Telegram).

Kullanım:
  python bot.py            -> bir tur: yeni mesajları işle, süresi gelen fiyatları kontrol et (GitHub Actions)
  python bot.py --surekli  -> bilgisayarında sürekli çalışır, mesajlara anında yanıt verir

Ortam değişkenleri:
  NTFY_EKLE         ntfy konusu: iPhone kısayolu linkleri/komutları buraya gönderir
  NTFY_BILDIRIM     ntfy konusu: bot bildirimleri buraya yollar (ikisi ayarlıysa ntfy kullanılır)
  TELEGRAM_TOKEN    BotFather'dan alınan token (ntfy ayarlı değilse)
  TELEGRAM_CHAT_ID  Sadece bu sohbetten gelen mesajlar kabul edilir (ilk mesajda bot söyler)
  KONTROL_DAKIKA    Bir ürünün fiyatı en fazla kaç dakikada bir kontrol edilsin (varsayılan 60)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fiyat import fiyat_oku

KOK = Path(__file__).resolve().parent
VERI = KOK / "data" / "urunler.json"

# Bilgisayarda çalışırken ayarlar .env dosyasından okunur (git'e girmez)
_env = KOK / ".env"
if _env.exists():
    for _satir in _env.read_text(encoding="utf-8-sig").splitlines():
        if "=" in _satir and not _satir.lstrip().startswith("#"):
            _k, _v = _satir.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
NTFY_SUNUCU = os.environ.get("NTFY_SUNUCU", "https://ntfy.sh").rstrip("/")
NTFY_EKLE = os.environ.get("NTFY_EKLE", "").strip()
NTFY_BILDIRIM = os.environ.get("NTFY_BILDIRIM", "").strip()
NTFY = bool(NTFY_EKLE and NTFY_BILDIRIM)
if NTFY:
    CHAT_ID = "ntfy"
KOMUTLAR = {"liste", "sil", "hedef", "kontrol", "yardim", "start", "help"}
KONTROL_DAKIKA = int(os.environ.get("KONTROL_DAKIKA", "60"))
URL_DESENI = re.compile(r"https?://[^\s<>\"']+")

YARDIM = (
    "🛍 <b>Fiyat Takip</b>\n\n"
    "Bir uygulamada veya sitede ürünü gördüğünde <b>Paylaş → Fiyat Takibe Ekle</b> "
    "ile linki gönder, takibe alırım. Fiyat düşünce haber veririm.\n\n"
    "/liste – takip edilen ürünler\n"
    "sil 3 – 3 numaralı ürünü sil\n"
    "hedef 3 799 – 3 numara 799 TL ve altına inince ayrıca haber ver\n"
    "kontrol – tüm fiyatları şimdi kontrol et"
)


# ---------- yardımcılar ----------

def simdi() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tl(x: float | None, para: str | None = "TRY") -> str:
    if x is None:
        return "?"
    metin = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if metin.endswith(",00"):
        metin = metin[:-3]
    return f"{metin} {'TL' if para in (None, 'TRY', 'TL') else para}"


def veri_oku() -> dict:
    if VERI.exists():
        return json.loads(VERI.read_text(encoding="utf-8"))
    return {"offset": 0, "sonraki_id": 1, "urunler": []}


def veri_yaz(veri: dict) -> None:
    VERI.parent.mkdir(parents=True, exist_ok=True)
    VERI.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")


def tg(metot: str, **params):
    url = f"https://api.telegram.org/bot{TOKEN}/{metot}"
    veri = urllib.parse.urlencode(params).encode()
    for deneme in range(4):
        try:
            with urllib.request.urlopen(url, data=veri, timeout=70) as r:
                sonuc = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            govde = json.loads(e.read() or b"{}")
            if e.code == 429 and deneme < 3:
                time.sleep(govde.get("parameters", {}).get("retry_after", 5) + 1)
                continue
            raise RuntimeError(f"Telegram hatası {e.code}: {govde.get('description')}") from None
    if not sonuc.get("ok"):
        raise RuntimeError(f"Telegram hatası: {sonuc}")
    return sonuc["result"]


def ntfy_gonder(metin: str) -> None:
    """Telegram için yazılmış HTML mesajı ntfy bildirimine çevirip yollar."""
    linkler = re.findall(r'<a href="([^"]+)">', metin)
    duz = re.sub(r'<a href="([^"]+)">(.*?)</a>', r"\2", metin)
    duz = html.unescape(re.sub(r"<[^>]+>", "", duz)).strip()
    baslik, _, govde = duz.partition("\n")
    if not govde:
        baslik, govde = "Fiyat Takip", baslik
    if len(linkler) > 1:  # liste: linkleri de yaz
        govde = duz
        baslik = "Takip listesi"
    istek = {"topic": NTFY_BILDIRIM, "title": baslik[:200], "message": govde[:3900]}
    if len(linkler) == 1:
        istek["click"] = html.unescape(linkler[0])
    r = urllib.request.Request(NTFY_SUNUCU + "/", data=json.dumps(istek).encode(),
                               headers={"Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=30).read()


def ntfy_cek(veri: dict) -> bool:
    since = veri.get("ntfy_since") or "12h"
    url = f"{NTFY_SUNUCU}/{NTFY_EKLE}/json?poll=1&since={urllib.parse.quote(since)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        satirlar = r.read().decode("utf-8").splitlines()
    var = False
    for satir in satirlar:
        olay = json.loads(satir)
        if olay.get("event") != "message":
            continue
        veri["ntfy_since"] = olay["id"]
        var = True
        try:
            mesaj_isle(veri, {"chat": {"id": "ntfy"}, "text": olay.get("message", "")})
        except Exception as e:
            print("Mesaj işlenemedi:", e, file=sys.stderr)
        veri_yaz(veri)
    return var


def gonder(chat_id, metin: str) -> None:
    if NTFY:
        try:
            ntfy_gonder(metin)
        except Exception as e:
            print("Bildirim gönderilemedi:", e, file=sys.stderr)
        return
    try:
        tg("sendMessage", chat_id=chat_id, text=metin, parse_mode="HTML",
           disable_web_page_preview="true")
    except Exception as e:
        print("Mesaj gönderilemedi:", e, file=sys.stderr)


def urun_bul(veri: dict, no: str):
    for u in veri["urunler"]:
        if str(u["id"]) == no:
            return u
    return None


def kisa(u: dict) -> str:
    return html.escape((u.get("baslik") or u["url"])[:70])


# ---------- komutlar ----------

def urun_ekle(veri: dict, chat_id, url: str) -> None:
    s = fiyat_oku(url)
    son = s.url or url
    if s.fiyat is None:
        site = urllib.parse.urlparse(son).netloc.removeprefix("www.") or "bu site"
        gonder(chat_id, f"❌ <b>{html.escape(site)}</b> sitesinden fiyat okunamıyor, ürün takibe alınmadı.\n"
                        f"Sebep: {html.escape(s.hata or 'bilinmiyor')}")
        return
    for u in veri["urunler"]:
        if u["url"] in (url, son):
            gonder(chat_id, f"ℹ️ Bu ürün zaten listede (#{u['id']}): {kisa(u)}")
            return
    u = {
        "id": veri["sonraki_id"],
        "url": son,
        "baslik": s.baslik,
        "para": s.para,
        "fiyat": s.fiyat,
        "ilk_fiyat": s.fiyat,
        "en_dusuk": s.fiyat,
        "stokta": s.stokta,
        "hedef": None,
        "hedef_bildirildi": False,
        "eklendi": simdi(),
        "son_kontrol": simdi(),
        "hata_sayisi": 0 if s.fiyat is not None else 1,
        "gecmis": [[simdi(), s.fiyat]] if s.fiyat is not None else [],
    }
    veri["sonraki_id"] += 1
    veri["urunler"].append(u)
    gonder(chat_id, f"✅ Takibe alındı (#{u['id']})\n{kisa(u)}\n"
                    f"Şu anki fiyat: <b>{tl(s.fiyat, s.para)}</b>\nStok: {stok_metni(s.stokta)}")


def stok_metni(stokta) -> str:
    if stokta is None:
        return "site stok bilgisi vermiyor"
    return "var ✅" if stokta else "yok ❌ (gelince haber vereceğim)"


def liste(veri: dict, chat_id) -> None:
    if not veri["urunler"]:
        gonder(chat_id, "Liste boş. Bir ürün linki paylaşarak başla.")
        return
    satirlar = []
    for u in veri["urunler"]:
        s = f"<b>#{u['id']}</b> <a href=\"{html.escape(u['url'])}\">{kisa(u)}</a>\n   {tl(u['fiyat'], u['para'])}"
        if u.get("en_dusuk") and u["fiyat"] and u["en_dusuk"] < u["fiyat"]:
            s += f" (en düşük {tl(u['en_dusuk'], u['para'])})"
        if u.get("hedef"):
            s += f" · hedef {tl(u['hedef'], u['para'])}"
        if u.get("stokta") is False:
            s += " · stokta yok"
        satirlar.append(s)
    for i in range(0, len(satirlar), 30):
        gonder(chat_id, "\n".join(satirlar[i:i + 30]))


def mesaj_isle(veri: dict, mesaj: dict) -> None:
    chat_id = mesaj["chat"]["id"]
    metin = (mesaj.get("text") or mesaj.get("caption") or "").strip()
    if metin.split(" ", 1)[0].lower() in KOMUTLAR:
        metin = "/" + metin  # ntfy'dan eğik çizgisiz komut gelebilir

    if not CHAT_ID:
        print(f"SOHBET_KIMLIGI={chat_id}")
        gonder(chat_id, f"Merhaba! Sohbet kimliğin: <code>{chat_id}</code>\n"
                        "Bunu <b>TELEGRAM_CHAT_ID</b> olarak ayarla, sonra linkleri gönderebilirsin.")
        return
    if str(chat_id) != CHAT_ID:
        return  # başkalarının mesajlarını yok say

    # Linkler (paylaşılan metinde başka yazılar da olabilir)
    linkler = URL_DESENI.findall(metin)
    for ent in mesaj.get("entities", []) + mesaj.get("caption_entities", []):
        if ent.get("type") == "text_link":
            linkler.append(ent["url"])
    if linkler and not metin.startswith("/"):
        for url in dict.fromkeys(l.rstrip(").,;") for l in linkler):
            urun_ekle(veri, chat_id, url)
        return

    parcalar = metin.split()
    komut = parcalar[0].split("@")[0].lower() if parcalar else ""
    if komut in ("/start", "/yardim", "/help"):
        gonder(chat_id, YARDIM)
    elif komut == "/liste":
        liste(veri, chat_id)
    elif komut == "/sil" and len(parcalar) >= 2:
        u = urun_bul(veri, parcalar[1].lstrip("#"))
        if u:
            veri["urunler"].remove(u)
            gonder(chat_id, f"🗑 Silindi: {kisa(u)}")
        else:
            gonder(chat_id, "Bu numarada ürün yok. /liste ile bakabilirsin.")
    elif komut == "/hedef" and len(parcalar) >= 3:
        u = urun_bul(veri, parcalar[1].lstrip("#"))
        from fiyat import sayi_coz
        hedef = sayi_coz(parcalar[2])
        if u and hedef:
            u["hedef"], u["hedef_bildirildi"] = hedef, False
            gonder(chat_id, f"🎯 #{u['id']} için hedef: {tl(hedef, u['para'])}")
        else:
            gonder(chat_id, "Kullanım: /hedef 3 799")
    elif komut == "/kontrol":
        gonder(chat_id, "🔄 Kontrol ediliyor…")
        n = fiyatlari_kontrol(veri, zorla=True)
        gonder(chat_id, f"Bitti, {n} ürün kontrol edildi.")
    else:
        gonder(chat_id, "Bir ürün linki gönder ya da /yardim yaz.")


def mesajlari_cek(veri: dict, bekle: int = 0) -> bool:
    guncellemeler = tg("getUpdates", offset=veri["offset"], timeout=bekle,
                       allowed_updates='["message"]')
    for g in guncellemeler:
        veri["offset"] = g["update_id"] + 1
        if "message" in g:
            try:
                mesaj_isle(veri, g["message"])
            except Exception as e:
                print("Mesaj işlenemedi:", e, file=sys.stderr)
        veri_yaz(veri)  # her mesajdan sonra kaydet
    return bool(guncellemeler)


# ---------- fiyat kontrolü ----------

def fiyatlari_kontrol(veri: dict, zorla: bool = False) -> int:
    sayi = 0
    esik = time.time() - KONTROL_DAKIKA * 60 + 120
    for u in list(veri["urunler"]):
        son = datetime.fromisoformat(u["son_kontrol"]).timestamp()
        if not zorla and son > esik:
            continue
        sayi += 1
        s = fiyat_oku(u["url"])
        u["son_kontrol"] = simdi()
        if s.fiyat is None:
            u["hata_sayisi"] = u.get("hata_sayisi", 0) + 1
            if u["hata_sayisi"] == 3 and CHAT_ID:
                gonder(CHAT_ID, f"⚠️ #{u['id']} fiyatı 3 kez okunamadı ({html.escape(s.hata or '')}): {kisa(u)}")
            continue
        u["hata_sayisi"] = 0
        u["baslik"] = u.get("baslik") or s.baslik
        eski, yeni = u.get("fiyat"), s.fiyat
        eski_stok = u.get("stokta")
        u["fiyat"], u["stokta"], u["para"] = yeni, s.stokta, s.para or u.get("para")
        if eski != yeni:
            u.setdefault("gecmis", []).append([simdi(), yeni])
            u["gecmis"] = u["gecmis"][-50:]
        if u.get("en_dusuk") is None or yeni < u["en_dusuk"]:
            u["en_dusuk"] = yeni
        if u.get("ilk_fiyat") is None:
            u["ilk_fiyat"] = yeni

        link = f"<a href=\"{html.escape(u['url'])}\">{kisa(u)}</a>"
        if eski is None:
            gonder(CHAT_ID, f"✅ #{u['id']} fiyatı okundu\n{link}\nŞu anki fiyat: <b>{tl(yeni, u['para'])}</b>")
        if eski is not None and yeni < eski - 0.5:
            yuzde = round((eski - yeni) / eski * 100)
            gonder(CHAT_ID, f"🔻 <b>İndirim! %{yuzde}</b>\n{link}\n{tl(eski, u['para'])} → <b>{tl(yeni, u['para'])}</b>")
        if u.get("hedef") and yeni <= u["hedef"] and not u.get("hedef_bildirildi"):
            u["hedef_bildirildi"] = True
            gonder(CHAT_ID, f"🎯 <b>Hedef fiyata ulaştı!</b>\n{link}\nŞimdi: <b>{tl(yeni, u['para'])}</b>")
        elif u.get("hedef") and yeni > u["hedef"]:
            u["hedef_bildirildi"] = False
        if eski_stok is False and s.stokta is True:
            gonder(CHAT_ID, f"📦 <b>Tekrar stokta!</b>\n{link}\n{tl(yeni, u['para'])}")
        elif eski_stok is True and s.stokta is False:
            gonder(CHAT_ID, f"🚫 <b>Stok tükendi</b>\n{link}\nTekrar gelince haber vereceğim.")
    return sayi


# ---------- giriş ----------

def main() -> None:
    if not NTFY and not TOKEN:
        sys.exit("NTFY_EKLE/NTFY_BILDIRIM veya TELEGRAM_TOKEN ayarlı değil.")
    cek = ntfy_cek if NTFY else mesajlari_cek
    veri = veri_oku()
    if "--surekli" in sys.argv:
        print("Bot çalışıyor. Durdurmak için Ctrl+C.")
        while True:
            try:
                if NTFY:
                    ntfy_cek(veri)
                    time.sleep(20)
                else:
                    mesajlari_cek(veri, bekle=50)
                if fiyatlari_kontrol(veri):
                    veri_yaz(veri)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print("Hata:", e, file=sys.stderr)
                time.sleep(10)
    else:
        once = _ozet(veri)
        cek(veri)
        fiyatlari_kontrol(veri)
        # Sadece kontrol saati değiştiyse dosyayı yazma (gereksiz commit olmasın)
        if _ozet(veri) != once:
            veri_yaz(veri)


def _ozet(veri: dict) -> str:
    kopya = json.loads(json.dumps(veri))
    for u in kopya.get("urunler", []):
        u.pop("son_kontrol", None)
    return json.dumps(kopya, sort_keys=True)


if __name__ == "__main__":
    main()
