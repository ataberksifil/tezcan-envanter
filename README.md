# Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi

## Project Status

- Gate 0 architecture/design completed
- Phase 1.1 — Repository Foundation completed
- Phase 1.2 — Django Bootstrap completed; Django foundation exists
- Phase 1.3 — PostgreSQL Environment completed; local PostgreSQL development environment exists

## Product Goal

Sistem bir malzemenin tanımlı depolama konumunda bulunduğunu söylüyorsa, malzeme fiziksel olarak orada bulunabilmelidir.

## Scope

**V1:** Elektrik Atölyesi envanter ve malzeme takibi.

Mekanik Atölye veya SAP entegrasyonu V1 kapsamında değildir.

## Architecture

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

## Local Database Configuration

PostgreSQL is required for local development. Environment variable names and example values are documented in [`.env.example`](.env.example). The local `.env` file is not committed.

## Repository Governance

Tüm katkıcılar ve AI agent'lar göreve başlamadan önce [AGENTS.md](AGENTS.md) dosyasını okumalıdır.

Kararlar ve açık hard gate'ler [docs/06-DECISION-REGISTER.md](docs/06-DECISION-REGISTER.md) içinde izlenir.

## Documentation

| Belge | Açıklama |
|---|---|
| [docs/00-PRODUCT.md](docs/00-PRODUCT.md) | Yetkili ürün gereksinimleri, V1 kapsamı ve başarı kriterleri |
| [docs/01-BUSINESS-RULES.md](docs/01-BUSINESS-RULES.md) | Onaylı gereksinimlerden türetilmiş doğrulanabilir iş kuralları |
| [docs/02-DOMAIN-MODEL.md](docs/02-DOMAIN-MODEL.md) | Kavramsal domain modeli ve entity ilişkileri |
| [docs/03-DATA-MODEL.md](docs/03-DATA-MODEL.md) | PostgreSQL/Django ORM yönünde ilişkisel veri modeli |
| [docs/04-USER-FLOWS.md](docs/04-USER-FLOWS.md) | Operasyonel kullanıcı akışları ve yetki beklentileri |
| [docs/05-ARCHITECTURE.md](docs/05-ARCHITECTURE.md) | Üretim odaklı teknik mimari ve modül sınırları |
| [docs/06-DECISION-REGISTER.md](docs/06-DECISION-REGISTER.md) | Karar durumları, hard gate'ler ve Gate 0 audit disposition'ları |

## Development Workflow

- Tek seferde bir kapsamlı görev
- İlgili otomatik testler
- Review
- Mantıksal tek Git commit
- Sonraki roadmap görevi otomatik başlatılmaz

## Current Roadmap Position

**Gate 0:** PASS

**Current:** Phase 1.3 — PostgreSQL Environment completed

**Next:** Phase 1.4 — App / Module Skeletons
