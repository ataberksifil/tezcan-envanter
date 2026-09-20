# Boss Demo Ortamı

Bu belge, yönetici/demo gösterimi için yerel demo ortamının kurulumunu ve önerilen walkthrough akışını tanımlar.

**Yalnızca yerel/dev kullanım.** Demo verisi fictional'dır; gerçek fabrika lokasyon/hat seed'i değildir (`DEC-023`, `DEC-025`). Operasyonel UI etiketlerinde lokasyon/hat yalnızca kısa kodlar (`G1`, `H1`) gösterilir. Stok yalnızca inventory service layer üzerinden oluşturulur; `StockBalance` veya `INITIAL_BALANCE` shortcut kullanılmaz.

---

## 1. Önkoşullar

- PostgreSQL çalışıyor olmalı
- Process ortamında DB kimlik bilgileri tanımlı (bkz. `.env.example`)
- Sanal ortam ve bağımlılıklar kurulu:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

---

## 2. Kurulum

```bash
python manage.py migrate
python manage.py setup_roles
python manage.py seed_demo_environment
python manage.py runserver
```

Tarayıcı: `http://127.0.0.1:8000/`

### Demo kullanıcıları

| Kullanıcı | Rol |
|---|---|
| `demo.yonetici` | ADMIN_MANAGER |
| `demo.depocu` | STOREKEEPER |
| `demo.teknisyen` | TECHNICIAN |

Yerel demo şifresi komut çıktısında gösterilir; production credential değildir.

Şifreleri yenilemek (mevcut demo verisini koruyarak):

```bash
python manage.py seed_demo_environment --reset-passwords
```

Komut idempotent'tir: demo marker lokasyonu (`G1`) zaten varsa stok hareketlerini tekrar oluşturmaz.

---

## 3. Seed edilen demo verisi

| Tür | Kod / tanım |
|---|---|
| Lokasyon | `G1`, `G2` |
| Mal kabul staging | `MK` (Mal Kabul, stok tutmaz), `MK-BEKLEYEN` (Yerleştirme Bekleyen, stok tutar). Production kodu bu kodlara sihirli sabit olarak bağlanmaz. |
| Malzeme (quantity) | `DEMO-KLEMENS` — terminal klemens |
| Malzeme (serialized) | `DEMO-PLC-CPU` |
| Çalışan | `DEMO-1001` Mehmet Teknisyen (demo.teknisyen ile bağlı) |
| Sayım oturumu | `DEMO-SAYIM-G1` rutin taslak (`G1`), `DEMO-KESIM-G2` kesim adayı taslak (`G2`) |

**Örnek ledger hareketleri (seed sonrası):**

- 100 adet klemens girişi → `G1`
- 1 adet serialized PLC girişi → `G1`
- 15 adet çıkış (teknisyen, hat `H1`)
- 5 adet iade (depocu)
- 10 adet transfer → `G2`

Beklenen klemens bakiyesi: `G1` ≈ 80, `G2` ≈ 10.

---

## 4. Önerilen boss walkthrough (~20 dk)

### A. Mevcut stok ve geçmiş

1. `/inventory/stock/` — klemens bakiyeleri (`G1`, `G2`)
2. `/inventory/transactions/` — RECEIPT, ISSUE, RETURN, TRANSFER geçmişi
3. Bir ISSUE detayı — alıcı snapshot, hat `H1`, kullanım yeri

### B. Stok girişi (`demo.depocu`)

4. `/catalog/materials/new/` — sistem kod üretir; model alanında Barkod Tara yardımcıdır
5. `/inventory/receipts/new/` — malzeme özeti, tam Location yolu, mal kabul konumuna kayıt; onay sonrası `Yerleştir / Transfer Et`
6. `/inventory/serialized-receipts/new/` (isteğe bağlı)

### C. Stok çıkışı (`demo.teknisyen`)

7. `/inventory/issues/new/` — hat seçiminde `H1`

### D. İade ve transfer (`demo.depocu`)

8. `/inventory/returns/new/`
9. `/inventory/transfers/new/`

### E. Düzeltme + kanıt (Phase 5.5)

10. Transaction detayından düzeltme talebi + fotoğraf kanıtı
11. `demo.yonetici`: `/corrections/` — onay/red
12. Authenticated evidence retrieval

### F. Yönetim (`demo.yonetici`)

13. `/management/`, `/locations/`, `/catalog/materials/`

### G. Fiziksel sayım ve kesim

14. `/counts/` — `DEMO-SAYIM-G1` (rutin, G1) ve `DEMO-KESIM-G2` (kesim adayı, G2) taslak oturumlar
15. `demo.depocu`: sayımı başlat, kör miktar/tekil sayım, tamamla
16. `demo.yonetici`: rutin fark onayı veya G2 kesim hazırlığı / kesim (hassas)

Ad-hoc açılış bakiyesi formu yoktur; INITIAL_BALANCE yalnız kontrollü kesimden oluşur.

---

## 5. Demo'da gösterilmeyecek özellikler

- Excel import UI
- Raporlama dashboard
- Serialized correction
- Talep Takip
- SKT / expiry workflow

---

## 6. Güvenlik notu

Demo kimlik bilgileri yalnızca yerel ortamdır. Production veya paylaşımlı sunucuda kullanmayın.
