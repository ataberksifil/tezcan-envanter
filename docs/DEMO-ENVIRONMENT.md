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
| Malzeme (quantity) | `DEMO-KLEMENS` — terminal klemens |
| Malzeme (serialized) | `DEMO-PLC-CPU` |
| Çalışan | `DEMO-1001` Mehmet Teknisyen (demo.teknisyen ile bağlı) |
| Üretim hattı | `H1` |

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

4. `/inventory/receipts/new/`
5. `/inventory/serialized-receipts/new/` (isteğe bağlı)

### C. Stok çıkışı (`demo.teknisyen`)

6. `/inventory/issues/new/` — hat seçiminde `H1`

### D. İade ve transfer (`demo.depocu`)

7. `/inventory/returns/new/`
8. `/inventory/transfers/new/`

### E. Düzeltme + kanıt (Phase 5.5)

9. Transaction detayından düzeltme talebi + fotoğraf kanıtı
10. `demo.yonetici`: `/corrections/` — onay/red
11. Authenticated evidence retrieval

### F. Yönetim (`demo.yonetici`)

12. `/management/`, `/locations/`, `/catalog/materials/`

---

## 5. Demo'da gösterilmeyecek özellikler

- Fiziksel sayım, baseline cutover, Excel import UI
- Serialized ISSUE/RETURN/TRANSFER
- Raporlama, QR/barcode

---

## 6. Güvenlik notu

Demo kimlik bilgileri yalnızca yerel ortamdır. Production veya paylaşımlı sunucuda kullanmayın.
