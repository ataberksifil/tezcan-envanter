# Proje Snapshot — Elektrik Atölyesi Envanter Sistemi

**Oluşturulma:** 2026-09-17  
**Branch:** `main`  
**Canonical inventory base:** `22c29deacb9247b3921a6f36a802ebe63ad9c341` (`feat: add serialized movement workflows`)
**Phase 5.6 backend:** implemented/committed (`DEC-035`)
**Phase 5.7 web workflows:** implemented/committed at `22c29de`
**Phase 5.8 identification:** COMPLETE at `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0` (`feat: add machine-readable identification`, `DEC-036`)

---

## 1. Özet

Modüler monolit Django 5.2 envanter sistemi; quantity envanter hareketleri, kontrollü düzeltme (kanıtlı), serialized RECEIVE, fiziksel sayım + combined baseline backend/UI ve serialized ISSUE/linked unused RETURN/in-stock TRANSFER akışları mevcuttur. Phase 5.7 state-aware normal Django web workflow'larını tamamlamıştır. Phase 5.8 carrier-neutral Machine-Readable Identification (Code128 standart 100 mm-sınıfı etiket, kompakt QR, canonical `TZ1M:<22-char-base64url-uuid>` / `TZ1A:<22-char-base64url-uuid>` / `TZ1L:<22-char-base64url-uuid>` payload, USB HID + kamera tarama, tek resolver) katmanı commit `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0` (`feat: add machine-readable identification`) üzerinde COMPLETE'tir. Count / Baseline Operational UI Closure commit `1dedd34051692e4c4743c63ead5fecd3c91e9229` (`feat: add count and baseline workflows`) üzerinde COMPLETE'tir.

**Son doğrulanmış full suite:** 1769 passed
**Collected test count:** 1769
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
| Phase 5.6 | Backend implemented/committed at `1947b8a` — serialized ISSUE / linked unused RETURN / in-stock TRANSFER (`DEC-035`) |
| Phase 5.7 | Web workflows committed at `22c29de` |
| Phase 5.8 | **COMPLETE** — Code128 + QR machine-readable identification (`DEC-036`) at `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0` |
| Count / baseline operational UI | **COMPLETE** at `1dedd34051692e4c4743c63ead5fecd3c91e9229` (`feat: add count and baseline workflows`); migration yok; kernels/services authoritative kaldı |
| Gate 3 | PASS (tarihsel; qty RECEIPT scope) |

---

## 4. Phase 5.8 — Tamamlanan Kapsam

| Özellik | Durum |
|---|---|
| Canonical payload | `TZ1M:<22-char-base64url-uuid>`, `TZ1A:<22-char-base64url-uuid>`, `TZ1L:<22-char-base64url-uuid>`; carrier-independent; UUID authoritative; no DB persistence |
| Standard label | Code128 SVG + human-readable identity; default workshop profile; committed |
| Compact label | QR PNG + human-readable identity; small labels / phone camera; committed |
| Scanner / resolver | Local ZXing multi-format camera (Code128 + QR) + USB HID/manual field; one authenticated POST resolver |
| Canonical navigation | Material, SerializedAsset ve Location detail; inactive records remain visible to authorized users |
| Quick actions | Existing Phase 5.7 asset ISSUE/RETURN/TRANSFER surfaces; no scanner mutation |
| Schema/kernel changes | None; existing inventory services remain authoritative |
| V1 exclusions | DataMatrix, RFID/NFC, printer driver, stored barcode token |

Phase 5.8 / `DEC-036` COMPLETE at `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0` (`feat: add machine-readable identification`).

## 4B. Count / Baseline Operational UI — Tamamlanan Kapsam

| Özellik | Durum |
|---|---|
| Physical count web workflow (`/counts/`) | Session list/detail, create/start, quantity/serialized count, unexpected/candidate observation, complete, quantity discrepancy review/approve/reject |
| Baseline web workflow (`/baselines/`) | Prepare, readiness preview, establish; `INITIAL_BALANCE` yalnız kontrollü kesimden |
| Service boundary | Mevut counting/imports authoritative servisleri; mutation kernel değişmedi |
| Schema | Migration yok |
| Serialized `COUNT_RECONCILIATION` | Backend servisi yok; UI bilgilendiricidir |
| `DEC-OPEN-010` | OPEN kalır |

Count / Baseline Operational UI Closure **COMPLETE** at `1dedd34051692e4c4743c63ead5fecd3c91e9229` (`feat: add count and baseline workflows`).

## 4A. Phase 5.5 — Tamamlanan Kapsam

| Özellik | Durum |
|---|---|
| Mandatory photographic evidence (new correction requests) | JPEG/PNG/WebP; kanıtsız `PENDING` yok |
| Protected evidence retrieval | `/corrections/evidence/<uuid>/` |
| Evidence retention / history | Onay/red kanıt korunur; V1 auto-delete yok; pre-5.5 grandfathered |
| Image validation / storage | 10 MiB/file; HEIC/HEIF red; private `var/private_media` |
| Evidence authorization | `corrections.view_correctionrequest`; public `MEDIA_URL` yok |

---

## 5. Kalan Backend İşi

Correction evidence closure, Phase 5.6 serialized movement backend, Phase 5.8 identification ve Count / Baseline Operational UI Closure uygulanmıştır; kalan ürün işi:

- Serialized correction
- Real Excel import workflow (UI)
- Reporting
- Final/global UI polish ve production-readiness iyileştirmeleri

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
