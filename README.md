# Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi

## Proje Durumu

- **Gate 0:** PASS
- **Phase 1 (1.1–1.8):** COMPLETE
- **Gate 1:** PASS
- **Phase 2:** COMPLETE
- **Gate 2:** PASS
- **Phase 2.8B:** DEFER (`DEC-OPEN-019` OPEN)
- **Phase 2.9C:** SKIPPED
- **Phase 3.0:** Location foundation decisions COMPLETE (`DEC-023`)
- **Phase 3.1:** Location foundation implementation COMPLETE
- **Phase 3.2-0:** Employee + ProductionLine decision pack COMPLETE (`DEC-024`, `DEC-025`)
- **Phase 3.2:** Employee foundation implementation COMPLETE
- **Phase 3.3:** ProductionLine foundation implementation COMPLETE
- **Inventory Core preflight:** PASS FOR NEXT IMPLEMENTATION (`DEC-026`)
- **Phase 4.0A:** MaterialCondition foundation COMPLETE (2026-09-12; commit `05bee71a`)
- **Phase 4.0B:** Quantity Inventory Kernel COMPLETE (2026-09-12; commit `4cf52669`)
- **Phase 4.0C:** First Mutation — quantity RECEIPT service — COMPLETE (2026-09-12; commit `4546739e`)
- **Phase 4.1:** Quantity RECEIPT UI + permission rollout — COMPLETE (2026-09-12; commit `927e83b2`)
- **Quantity RECEIPT:** onaylı quantity-only slice uçtan uca implement edilmiştir (kernel, service, UI, `inventory.receive_stock` permission rollout)
- **Phase 4.2:** Quantity ISSUE — COMPLETE (2026-09-12; commits `bc77b50`, `e99a6c3`, `077e9d5`)
- **Phase 4.3:** Inventory Transaction History — COMPLETE (2026-09-12; commit `cec8284`)
- **Phase 4.4:** Quantity unused linked RETURN — COMPLETE (2026-09-12; commits `e96ec2d`, `3e68f02`, `a98cf87`)
- **Quantity RETURN first slice:** uçtan uca implement edilmiştir (kernel, service, UI, `inventory.return_stock` permission rollout)
- **Phase 4.5:** Quantity TRANSFER — COMPLETE
- **Phase 5.4:** Physical Count + Combined Quantity/Serialized Baseline backend + operational UI COMPLETE (Phase 5.4E permission rollout dahil; UI commit `1dedd34`)
- **Phase 5.5:** Controlled Correction Evidence / Photo Closure COMPLETE (`DEC-034`)
- **Son doğrulanmış test suite:** 1568 passed
- **Managed permission count:** 28
- **Gate 3:** PASS (2026-09-12)

Phase 2 ayrıntıları için bkz. [Yol Haritası](#yol-haritası).

## Ürün Hedefi

Sistem bir malzemenin tanımlı depolama konumunda bulunduğunu söylüyorsa, malzeme fiziksel olarak orada bulunabilmelidir.

## Kapsam

**V1:** Elektrik Atölyesi envanter ve malzeme takibi.

Mekanik Atölye veya SAP entegrasyonu V1 kapsamında değildir.

## Mimari

- Modüler monolit
- Python
- Django 5.2 LTS
- Django Templates
- HTMX
- Bootstrap 5
- PostgreSQL
- Django ORM
- Django Auth
- pytest/Django tests
- openpyxl
- local filesystem via Django storage abstraction

Tam mimari ayrıntıları için bkz. [docs/05-ARCHITECTURE.md](docs/05-ARCHITECTURE.md).

## Yerel Veritabanı Yapılandırması

Yerel geliştirme için PostgreSQL gereklidir. Ortam değişkeni adları ve örnek değerler [`.env.example`](.env.example) dosyasında belgelenmiştir. Yerel `.env` dosyası commit edilmez.

## Ortam ve Çalışma Zamanı Yapılandırması

- Ortam değişkenleri shell, process manager veya deployment ortamı tarafından dışarıdan sağlanır.
- [`.env.example`](.env.example) yalnızca referans/şablondur; Django `.env` dosyalarını otomatik yüklemez.
- Statik kaynak dosyaları `core/static/` gibi app static dizinlerinde bulunur.
- `collectstatic` çıktı hedefi: `var/static`
- Runtime upload hedefi: `var/media`
- Private correction evidence: `var/private_media` (`MEDIA_URL` üzerinden servis edilmez)
- `var/` kasıtlı olarak Git-ignore edilmiş üretilmiş/runtime veridir.
- Uygulama logları konsola gider.
- Uygulama saat dilimi `Europe/Istanbul`, `USE_TZ=True`.

## Repositori Yönetişimi

Tüm katkıcılar ve AI agent'lar göreve başlamadan önce [AGENTS.md](AGENTS.md) dosyasını okumalıdır.

Kararlar ve açık hard gate'ler [docs/06-DECISION-REGISTER.md](docs/06-DECISION-REGISTER.md) içinde izlenir.

## Dokümantasyon

| Belge | Açıklama |
|---|---|
| [docs/00-PRODUCT.md](docs/00-PRODUCT.md) | Yetkili ürün gereksinimleri, V1 kapsamı ve başarı kriterleri |
| [docs/01-BUSINESS-RULES.md](docs/01-BUSINESS-RULES.md) | Onaylı gereksinimlerden türetilmiş doğrulanabilir iş kuralları |
| [docs/02-DOMAIN-MODEL.md](docs/02-DOMAIN-MODEL.md) | Kavramsal domain modeli ve entity ilişkileri |
| [docs/03-DATA-MODEL.md](docs/03-DATA-MODEL.md) | PostgreSQL/Django ORM yönünde ilişkisel veri modeli |
| [docs/04-USER-FLOWS.md](docs/04-USER-FLOWS.md) | Operasyonel kullanıcı akışları ve yetki beklentileri |
| [docs/05-ARCHITECTURE.md](docs/05-ARCHITECTURE.md) | Üretim odaklı teknik mimari ve modül sınırları |
| [docs/06-DECISION-REGISTER.md](docs/06-DECISION-REGISTER.md) | Karar durumları, hard gate'ler ve Gate 0 audit disposition'ları |
| [docs/PROJECT-SNAPSHOT.md](docs/PROJECT-SNAPSHOT.md) | Güncel proje snapshot (HEAD, faz durumu, kalan kapsam) |
| [docs/DEMO-ENVIRONMENT.md](docs/DEMO-ENVIRONMENT.md) | Boss demo ortamı kurulumu ve walkthrough |

## Geliştirme ve Test Kurulumu

Repositori-yerel sanal ortam oluşturun ve kullanın:

```bash
python -m venv .venv
```

Windows etkinleştirme:

```bash
.venv\Scripts\activate
```

Geliştirme/test bağımlılıklarını kurun:

```bash
python -m pip install -r requirements-dev.txt
```

Test suite'i çalıştırın:

```bash
pytest
```

Test notları:

- Testler yalnız PostgreSQL kullanır; SQLite desteklenmez.
- Yerel test veritabanı `test_tezcan_envanter`; geliştirme veritabanı `tezcan_envanter`'dan ayrıdır.
- Test veritabanı önceden oluşturulur ve kasıtlı olarak `NOCREATEDB` kalan `tezcan_envanter` uygulama rolüne aittir.
- `pytest.ini` varsayılan olarak `--reuse-db` kullanır; pytest `CREATE DATABASE` denemek yerine önceden oluşturulmuş test veritabanını yeniden kullanır.
- Gerçek `.env` dosyaları otomatik yüklenmez; testlerden önce PostgreSQL kimlik bilgilerini shell veya process ortamınız üzerinden sağlayın.
- Normal test komutu `pytest`'tir; `--reuse-db` migration değişikliklerinde şemayı otomatik yeniden oluşturmaz. Migration/şema değişikliklerinden sonra `test_tezcan_envanter` kontrollü DBA/admin yenilemesi gerektirebilir. Kısayol olarak `tezcan_envanter`'a `CREATEDB` vermeyin.

## Yerel / Dağıtım Başlangıç Adımları

PostgreSQL kimlik bilgileri process ortamında hazır olduktan sonra:

```bash
python manage.py migrate
python manage.py setup_roles
python manage.py seed_demo_environment   # isteğe bağlı boss demo verisi
python manage.py runserver
```

`setup_roles` eksik varsayılan rolleri (`TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER`) başlangıç catalog permission şablonlarıyla bootstrap eder. Mevcut roller ve yönetici permission değişiklikleri korunur; komut zaten var olan gruplarda permission ekleme/çıkarma/reconcile yapmaz. Deployment/bootstrap provisioning'dir ve `AuditEvent` satırı yazmaz.

Boss demo ortamı ayrıntıları için bkz. [docs/DEMO-ENVIRONMENT.md](docs/DEMO-ENVIRONMENT.md). Güncel proje durumu için bkz. [docs/PROJECT-SNAPSHOT.md](docs/PROJECT-SNAPSHOT.md).

## Geliştirme İş Akışı

- Tek seferde bir kapsamlı görev
- İlgili otomatik testler
- Review
- Mantıksal tek Git commit
- Sonraki roadmap görevi otomatik başlatılmaz

## Yol Haritası

- **Gate 0:** PASS
- **Phase 1 (1.1–1.8):** tamamlandı
- **Gate 1:** PASS

**Phase 2:** TAMAMLANDI — **Gate 2 PASS** (2026-09-11)

- Phase 2.5C — Non-destructive role bootstrap hardening
- Phase 2.6 — UnitOfMeasure UI
- Phase 2.7 — Material list/search/detail
- Phase 2.8A — Material base writes
- Phase 2.8B — Technical-specification gate **DEFER** (`DEC-OPEN-019` OPEN)
- Phase 2.9A — Yönetim/configuration shell
- Phase 2.9B — Dynamic roles/permissions/user assignment
- Phase 2.9C — Technical-field configuration **SKIPPED** (onaylı gerçek fabrika teknik alan kanıtı yok; Phase 2.8B DEFER otoritatif)
- Phase 2.10 — Gate 2 **PASS**

**Phase 3.0:** Location foundation decisions — COMPLETE (`DEC-023`, 2026-09-11)

**Phase 3.1:** Location foundation implementation — COMPLETE

**Phase 3.2-0:** Employee + ProductionLine decision pack — COMPLETE (`DEC-024`, `DEC-025`, 2026-09-11)

**Phase 3.2:** Employee foundation implementation — COMPLETE (2026-09-12; commit `8a759cbf`)

**Phase 3.3:** ProductionLine foundation implementation — COMPLETE (2026-09-12; commit `418d49e4`)

**Inventory Core preflight:** PASS FOR NEXT IMPLEMENTATION (`DEC-026`, 2026-09-12)

**Phase 4.0A:** MaterialCondition foundation — COMPLETE (2026-09-12; commit `05bee71a`)

**Phase 4.0B:** Quantity Inventory Kernel — COMPLETE (2026-09-12; commit `4cf52669`)

**Phase 4.0C:** First Mutation — quantity RECEIPT service — COMPLETE (2026-09-12; commit `4546739e`)

**Phase 4.1:** Quantity RECEIPT UI + permission rollout — COMPLETE (2026-09-12; commit `927e83b2`)

**Phase 4.2:** Quantity ISSUE — COMPLETE (2026-09-12)

- kernel: `bc77b50`
- service: `e99a6c3`
- UI/workflow: `077e9d5`

**Phase 4.3:** Inventory Transaction History — COMPLETE (2026-09-12; commit `cec8284`)

**Phase 4.4:** Quantity unused linked RETURN — COMPLETE (2026-09-12)

- kernel + DEC-028: `e96ec2d`
- service / concurrency / projection: `3e68f02`
- UI + permission rollout: `a98cf87`

Fresh-role policy (`inventory.return_stock`): TECHNICIAN no; STOREKEEPER yes; ADMIN_MANAGER yes.

Broader RETURN senaryoları deferred: serialized, used/removed goods, defective/condition-changing, unknown provenance, supplier rejection, technician-originated approval, correction/count interactions.

**Phase 4.5:** Quantity TRANSFER — COMPLETE

Fresh-role policy (`inventory.transfer_stock`): TECHNICIAN no; STOREKEEPER yes; ADMIN_MANAGER yes.

Broader TRANSFER senaryoları deferred: serialized, condition-changing, multi-source/multi-target, FIFO/FEFO, technician custody, person-to-person handover, production usage, correction/count, QR/offline.

**Phase 5.4:** Physical Count + Combined Quantity/Serialized Baseline backend + operational UI — COMPLETE. Backend: count session foundation, quantity/serialized count, blind count, explicit zero/NOT_COUNTED, routine COUNT_RECONCILIATION, SoD discrepancy approval, combined baseline/INITIAL_BALANCE, drift detection, serialized candidate promotion, projection verification, permission rollout. Operational UI COMPLETE at `1dedd34051692e4c4743c63ead5fecd3c91e9229` (`feat: add count and baseline workflows`); `/counts/` ve `/baselines/`; migration yok; kernels/services authoritative kaldı; serialized `COUNT_RECONCILIATION` backend'i icat edilmedi. Serialized correction, serialized `COUNT_RECONCILIATION` ve custody uygulanmamıştır. `DEC-OPEN-010` OPEN kalır.

**Phase 5.5:** Controlled Correction Evidence / Photo Closure — COMPLETE (`DEC-034`). Yeni quantity `CorrectionRequest` için en az bir JPEG/PNG/WebP kanıt zorunludur; HEIC/HEIF desteklenmez; dosya başına 10 MiB; otomatik silme yoktur; authenticated `/corrections/evidence/<uuid>/` retrieval `corrections.view_correctionrequest` ile korunur; public `MEDIA_URL` yoktur; historical pre-5.5 talepler grandfathered'dır. Serialized correction deferred kalır.

**Phase 5.6:** Serialized ISSUE + linked unused RETURN + in-stock TRANSFER backend commit `1947b8ab374510c8bafb5f58f160decc455969d6` üzerinde uygulanmıştır (`DEC-035`).

**Phase 5.7:** State-aware normal Django web workflow/UI entegrasyonu commit `22c29deacb9247b3921a6f36a802ebe63ad9c341` üzerinde tamamlanmıştır. Kanonik tekil varlık detayı yalnız geçerli state + permission aksiyonlarını gösterir; quantity/unit veya condition transformation yüzeyi yoktur.

**Phase 5.8:** `DEC-036` Machine-Readable Identification & Scanning katmanı COMPLETE at `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0` (`feat: add machine-readable identification`). Canonical payload `TZ1M:<22-char-base64url-uuid>`, `TZ1A:<22-char-base64url-uuid>`, `TZ1L:<22-char-base64url-uuid>`; Code128 standart 100 mm-sınıfı etiket / kompakt QR committed; USB HID/klavye-wedge, yerel kamera tarayıcı, manuel fallback ve mevcut state-aware movement aksiyonlarına güvenli navigation; şema/migration ve scan mutation engine yoktur.

**Inbound / Mal Kabul V1 first slice (`DEC-037`):** Implemented, uncommitted, review bekliyor. Sistem üretilen `MAT-########` kodu, STOREKEEPER+ADMIN Material create, model barkod yardımcısı, keyword search, quantity RECEIVE UX + demo staging Location + mevcut TRANSFER putaway/etiket devamı. Talep Takip, SKT ve generic UoM conversion bu dilimde yoktur; TZ1 payload değişmez.

**Son doğrulanmış test suite:** 1769 passed (Count/baseline UI commit `1dedd34051692e4c4743c63ead5fecd3c91e9229`; Phase 5.8 at `c53a34a4b33e060e5f6365a9f191a1f24b1f17f0`; reused test DB MaterialCondition seeds restored via committed `0004_seed_material_conditions`)

**Managed permission count:** 28

**Gate 3:** PASS (2026-09-12; audited HEAD `4cf89525`; bkz. [docs/06-DECISION-REGISTER.md](docs/06-DECISION-REGISTER.md) §4.5). Tarihsel kapsam: Phase 3 (Location, Employee, ProductionLine) ve quantity-only RECEIPT slice (4.0A–4.1). Phase 4.2 ISSUE, Phase 4.3 history, Phase 4.4 RETURN first slice ve Phase 4.5 quantity TRANSFER first slice sonradan implement edilmiştir; Gate 3 bunları audit etmemiştir. Serialized inventory, correction ve count/baseline sonradan Phase 5.3–5.4D-B backend dilimlerinde implement edilmiştir; Gate 3 bunları audit etmemiştir. Count/baseline operational UI commit `1dedd34` üzerinde COMPLETE'tir.

### Sağlık kontrolü

`GET /health/` — kimlik doğrulama gerekmez.

- `200` — Django process çalışıyor ve PostgreSQL yanıt veriyor
- `503` — PostgreSQL kullanılamıyor
