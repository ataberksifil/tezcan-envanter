# Proje Snapshot — Elektrik Atölyesi Envanter Sistemi

**Oluşturulma:** 2026-09-17  
**Branch:** `main`  
**Canonical inventory base:** `5ec22eefc3cb80a355ce32d88170feef174d8a35` (`feat: add correction evidence`)  
**Phase 5.5:** COMPLETE (`DEC-034`)

---

## 1. Özet

Modüler monolit Django 5.2 envanter sistemi; quantity envanter hareketleri, kontrollü düzeltme (kanıtlı), serialized RECEIVE, fiziksel sayım + combined baseline backend ve ilgili operasyonel UI katmanları tamamlanmış durumdadır.

**Son doğrulanmış full suite (Phase 5.5 base):** 1568 passed in 6m 55s  
**Collected test count (post demo-seed test):** 1569  
**Managed permission count:** 28

---

## 2. Mekanik Repository Metrikleri

Aşağıdaki sayılar **git object `5ec22eefc3cb80a355ce32d88170feef174d8a35`** üzerinde mekanik olarak ölçülmüştür (Phase 5.5 tamamlanmış canonical base; demo snapshot aracı hariç).

| Metrik | Değer |
|---|---:|
| Git-tracked files | 286 |
| Total physical LOC | 64,526 |
| Production Python LOC | 16,150 |
| Test LOC | 31,831 |
| Migration LOC | 6,426 |
| HTML/template LOC | 2,968 |
| CSS LOC | 31 |
| JavaScript LOC | 8 |
| Documentation LOC | 6,978 |
| Django app count | 10 |
| Django apps | `accounts`, `audit`, `catalog`, `core`, `corrections`, `counting`, `imports`, `inventory`, `locations`, `reports` |
| Model count | 23 |
| Committed migration count | 28 |
| Route count | 110 |
| Template count | 40 |
| Git commit count | 65 |
| Test LOC / production LOC ratio | 1.971 |
| Largest production source file | `counting/services.py` — **1,332 LOC** |

### Top 10 largest production source files (base `5ec22ee`)

| LOC | File |
|---:|---|
| 1,332 | `counting/services.py` |
| 918 | `imports/services.py` |
| 827 | `counting/models.py` |
| 774 | `inventory/forms.py` |
| 754 | `inventory/views.py` |
| 597 | `inventory/services/receipts.py` |
| 567 | `catalog/views.py` |
| 554 | `inventory/models.py` |
| 543 | `accounts/services/access_management.py` |
| 495 | `inventory/services/corrections.py` |

### Top 3 apps by production LOC (base `5ec22ee`)

| App | Production LOC |
|---|---:|
| `inventory` | 6,125 |
| `counting` | 2,265 |
| `accounts` | 1,901 |

**Not:** Bu commit demo snapshot/seed aracını içermez. Demo commit sonrası collected test count **1569** (+1 seed command test).

---

## 3. Tamamlanan Fazlar

| Faz | Durum |
|---|---|
| Gate 0 / Phase 1 / Gate 1 | PASS / COMPLETE |
| Phase 2 | COMPLETE (2.8B DEFER, 2.9C SKIPPED) |
| Gate 2 | PASS |
| Phase 3.0–3.3 | COMPLETE |
| Phase 4.0A–4.5 | COMPLETE |
| Phase 5.1–5.4 | COMPLETE |
| **Phase 5.5** | **COMPLETE** — correction evidence (`DEC-034`) |
| Gate 3 | PASS (tarihsel; qty RECEIPT scope) |

---

## 4. Phase 5.5 — Tamamlanan Kapsam

| Özellik | Durum |
|---|---|
| Mandatory photographic evidence (new correction requests) | JPEG/PNG/WebP; kanıtsız `PENDING` yok |
| Protected evidence retrieval | `/corrections/evidence/<uuid>/` |
| Evidence retention / history | Onay/red kanıt korunur; V1 auto-delete yok; pre-5.5 grandfathered |
| Image validation / storage | 10 MiB/file; HEIC/HEIF red; private `var/private_media` |
| Evidence authorization | `corrections.view_correctionrequest`; public `MEDIA_URL` yok |

---

## 5. Kalan Backend İşi

Correction evidence closure **tamamlandı**; kalan backend:

- Serialized ISSUE / RETURN / TRANSFER
- Serialized correction
- Real Excel import workflow (UI)
- Reporting
- QR / barcode
- Count / baseline UI + remaining polish

---

## 6. Demo Hazırlığı

Boss demo: [DEMO-ENVIRONMENT.md](DEMO-ENVIRONMENT.md)

```bash
python manage.py migrate
python manage.py setup_roles
python manage.py seed_demo_environment
python manage.py runserver
```

Demo operasyonel etiketler: lokasyon **`G1`**, üretim hattı **`H1`** (expanded master adlar ordinary UI'da gösterilmez).

---

## 7. Yetkili Belgeler

`docs/00-PRODUCT.md` … `docs/06-DECISION-REGISTER.md`, `AGENTS.md`
