# Fiyat Takip

iPhone'da bir ürün gördüğünde **Paylaş → Fiyat Takibe Ekle** ile gönder; fiyat düşünce, hedef fiyata inince
veya stoğa geri girince **ntfy** uygulamasından bildirim gelir. GitHub Actions 5 dakikada bir çalışır.

> Telegram desteği de kodda duruyor (`TELEGRAM_TOKEN`), ama Kasım 2026'da Telegram yeni botlara 429 hatası
> verdiği için şu an ntfy kullanılıyor. İkisi birden ayarlıysa ntfy kullanılır.

## Ayarlar (GitHub → Settings → Secrets → Actions)
| Secret | Ne |
|---|---|
| `NTFY_EKLE` | Kısayolun linkleri gönderdiği ntfy konusu (gizli, rastgele bir ad) |
| `NTFY_BILDIRIM` | Bildirimlerin geldiği ntfy konusu (gizli, rastgele bir ad) |

Konu adları şifre gibidir: adı bilen herkes o konuya yazabilir ve okuyabilir. Kimseyle paylaşma.

## iPhone kurulumu
1. App Store'dan **ntfy** uygulamasını kur → **+** → `NTFY_BILDIRIM` konusuna abone ol → bildirim izni ver.
2. **Kestirmeler (Shortcuts)** uygulaması → **+** → adı: **Fiyat Takibe Ekle**
   - Eylem ekle: **URL'nin İçeriğini Al** (Get Contents of URL)
     - URL: `https://ntfy.sh/`
     - Yöntem: **POST**, İstek Gövdesi: **JSON**
     - Alan `topic` (Metin) = `NTFY_EKLE` konusunun adı
     - Alan `message` (Metin) = **Kestirme Girdisi** (Shortcut Input)
   - Eylem ekle: **Bildirim Göster** → "Takibe gönderildi"
   - Ayrıntılar (ⓘ): **Paylaşma Sayfasında Göster** açık; girdi türü: **URL'ler** ve **Metin**
3. Herhangi bir uygulamada ürün → **Paylaş** → **Fiyat Takibe Ekle**.

## Komutlar
Kısayolu Kestirmeler uygulamasından elle çalıştırıp metin olarak yazabilirsin
(ya da ntfy uygulamasında `NTFY_EKLE` konusuna mesaj gönder):

| Komut | Ne yapar |
|---|---|
| *(link)* | Takibe alır |
| `liste` | Takip edilenleri bildirim olarak yollar |
| `sil 3` | 3 numarayı siler |
| `hedef 3 799` | 799 TL ve altına inince ayrıca haber verir |
| `kontrol` | Hepsini şimdi kontrol eder |

Fiyat okunamayan sitelerin linkleri listeye eklenmez, bot hangi sitenin okunamadığını bildirir.
Site stok bilgisi veriyorsa stok tükenince ve geri gelince de bildirim gelir.

Cevaplar bir sonraki Actions çalışmasında gelir (genelde 5-15 dk içinde). Hemen çalıştırmak için:
GitHub → Actions → Fiyat Takip → Run workflow.

## Bilgisayarda çalıştırma (isteğe bağlı, anında cevap)
`.env` dosyasına `NTFY_EKLE=...` ve `NTFY_BILDIRIM=...` yaz, sonra:
```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt; .venv\Scripts\python bot.py --surekli
```
GitHub Actions ile aynı anda çalıştırma.

## Hangi siteler çalışır?
Fiyatı, sayfanın kendi yayınladığı ürün verisinden (schema.org JSON-LD / meta etiketleri) okur.
Trendyol'da test edildi. Bot koruması olan sitelerde (şu an Amazon, Zara, Bershka, Pull&Bear)
"fiyatı okuyamadım" bildirimi gelir.
