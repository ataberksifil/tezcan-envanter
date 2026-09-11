# Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi

## Proje Durumu

- **Gate 0:** PASS
- **Phase 1 (1.1–1.8):** tamamlandı
- **Gate 1:** PASS
- **Phase 2:** TAMAMLANDI
- **Gate 2:** PASS
- **Phase 2.8B:** DEFER (`DEC-OPEN-019` OPEN)
- **Phase 2.9C:** SKIPPED
- **Sıradaki:** Phase 3.1 — Location foundation implementation
- **Phase 3.0:** Location foundation decisions COMPLETE (`DEC-023`)
- **İlk çalışma:** Location henüz implement edilmemiştir

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
```

`setup_roles` eksik varsayılan rolleri (`TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER`) başlangıç catalog permission şablonlarıyla bootstrap eder. Mevcut roller ve yönetici permission değişiklikleri korunur; komut zaten var olan gruplarda permission ekleme/çıkarma/reconcile yapmaz. Deployment/bootstrap provisioning'dir ve `AuditEvent` satırı yazmaz.

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

**Sıradaki:** Phase 3.1 — Location foundation implementation. Location henüz implement edilmemiştir.

Inventory mutation implementasyonu henüz başlamamıştır. Açık hard gate'ler (`DEC-HG-001`–`DEC-HG-005`, `DEC-OPEN-005`, `DEC-OPEN-010`, `DEC-OPEN-011`, `DEC-OPEN-019`, `DEC-OPEN-021` Material remainder) korunur.

### Sağlık kontrolü

`GET /health/` — kimlik doğrulama gerekmez.

- `200` — Django process çalışıyor ve PostgreSQL yanıt veriyor
- `503` — PostgreSQL kullanılamıyor
