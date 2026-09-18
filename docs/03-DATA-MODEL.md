# Data Model — Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi

## 1. Belge Amacı

Bu belge, `docs/02-DOMAIN-MODEL.md` içindeki domain modelini PostgreSQL ve ilerideki Django ORM uygulamasına uygun, üretim odaklı ilişkisel veri modeline dönüştürür. `docs/00-PRODUCT.md` ve `docs/01-BUSINESS-RULES.md` içindeki gereksinim ve invariant'lar bu tasarımın sınırlarıdır.

Belge yürütülebilir SQL, Django modeli veya migration içermez. Tablo ve kısıtlar kavramsaldır; açık iş kararları varsayımla kapatılmaz.

Önerilen uygulama tablosu sayısı **24**'tür. `inventory_baseline_count_session_links` ve `inventory_baseline_transaction_links` scoped cutover ilişkileri bu sayıya dahildir. Django'nun auth tabloları bu sayıya dahil değildir ve bu belgede yeniden tasarlanmamıştır.

## 2. Veri Modeli İlkeleri

1. `inventory_transactions` ve satırları yetkili stok ledger'ıdır.
2. `stock_balances` yalnızca miktar bazlı güncel durum projection'ıdır.
3. `materials` üzerinde `stock_quantity` benzeri yetkili değişken stok alanı bulunmaz.
4. Quantity ve serialized envanter aynı satırda karıştırılamaz.
5. `transaction_type` ile `material_condition` farklı kavram ve alanlardır.
6. Tamamlanmış ledger kayıtları PostgreSQL DB-level guard ile immutable/no-delete'dir.
7. Düzeltme özgün işlemi değiştirmez; talep ve sonuç işlemiyle ilişkilendirir.
8. Fiziksel sayım farkı doğrudan bakiye üzerine yazılmaz.
9. Excel import commit'i stok ledger'ı veya `StockBalance` oluşturmaz; candidate data staging-only'dir.
10. İş entity'lerinde UUID birincil anahtar, iş zamanlarında timezone-aware timestamp tercih edilir.
11. Tarihsel tablolar hard delete ile kaybedilmez; master kayıtlar referans varsa pasifleştirilir.
12. V1 tasarımı Django ORM ile yönetilebilir kalır; event sourcing veya mikroservis altyapısı gerektirmez.

## 3. Veri Tipi ve Kimlik Standartları

| Konu | Öneri | Gerekçe |
|---|---|---|
| Birincil anahtar | `UUID` | İş/domain entity'lerinde dağıtık üretim ve güvenli dış referans için. |
| Teknik zaman | `TIMESTAMPTZ` | UTC tabanlı saklama ve fabrika yerel saatine güvenli dönüşüm için. |
| Miktar | `NUMERIC(18,3)` | 12.75 metre gibi ondalıklı miktarları ve yeterli endüstriyel aralığı destekler. |
| Kısa kod | `VARCHAR` veya sınırlı `TEXT` | Uzunluk ilgili feature implementation öncesi kesinleştirilir. |
| Açıklama | `TEXT` | İş açıklamalarında yapay kısa sınırdan kaçınır. |
| Esnek veri | `JSONB` | Teknik nitelik ve import staging verisi için. |
| Boolean | `BOOLEAN` | Aktiflik ve fiziksel sayım var/yok değerleri için. |
| Satır sırası | Pozitif `INTEGER` | İşlem veya import içi kararlı sıra için. |

`created_at` kaydın sisteme eklenme, `updated_at` değiştirilebilir master/staging kaydının son güncellenme zamanıdır. Immutable ledger satırlarında `updated_at` önerilmez. `occurred_at`, sistemce kaydedilen iş olayı zamanıdır ve normal kullanıcı tarafından düzenlenemez.

## 4. Identity / Employee Tabloları

### 4.1 `employees`

**Purpose:** Fabrika çalışanı/teslim alan kişi kimliğini temsil eder.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Sistem içi kimlik. |
| `employee_number` | VARCHAR | Hayır | UNIQUE | Çalışan sicil numarası; string; leading zero korunur; case-sensitive global unique; editable (`DEC-024`). |
| `first_name` | VARCHAR | Hayır | Boş olamaz | Güncel ad. |
| `last_name` | VARCHAR | Hayır | Boş olamaz | Güncel soyad. |
| `active` | BOOLEAN | Hayır | Default true | Kullanım durumu. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `user_id` | Auth user PK tipi | Evet | FK, UNIQUE | Nullable one-to-one `accounts.User` bağlantısı; ownership Employee tarafında (`DEC-024`). |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `user_id → accounts.User`; delete `SET_NULL`.
- **Unique Constraints:** `employee_number` globally unique (case-sensitive); `user_id` unique when not null.
- **Check Constraints:** Ad, soyad ve sicil numarası outer trim sonrası boş olamaz.
- **Recommended Indexes:** Unique `employee_number`; unique partial `user_id`; gerektiğinde `last_name, first_name`.
- **Delete Policy:** Hard delete application surface yok; `active/inactive` lifecycle. User silinince link `SET_NULL`. Transaction snapshot'ları çalışan değişikliğinden etkilenmez.
- **Notes / TBD:** Eski sicil numarası ayrı historical registry ile rezerve edilmez; import/matching immutable identity key varsaymamalıdır. Gelecek audit: `accounts.employee.created/updated/deactivated/reactivated`. Permissions: `accounts.view_employee`, `accounts.add_employee`, `accounts.change_employee` (`DEC-024`). Employee foundation Phase 3.2'de implement edilmiştir.

## 5. Catalog Tabloları

### 5.1 `categories`

**Purpose:** Hiyerarşik malzeme kategori ağacını tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Kategori kimliği. |
| `code` | VARCHAR | Evet | Benzersizlik TBD | Opsiyonel iş kodu. |
| `name` | VARCHAR | Hayır | Boş olamaz | Kategori adı. |
| `parent_id` | UUID | Evet | Self FK | Üst kategori. |
| `active` | BOOLEAN | Hayır | Default true | Kullanım durumu. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `parent_id → categories.id`, delete restricted.
- **Unique Constraints:** `code` kullanılacaksa global unique önerilir; kod standardı TBD'dir.
- **Check Constraints:** `parent_id <> id`.
- **Recommended Indexes:** `parent_id`, `name`, opsiyonel unique `code`.
- **Delete Policy:** Referans veya alt kategori varsa `SOFT DELETE / DEACTIVATE`; tarihsel bağları bozacak hard delete yok.
- **Notes / TBD:** Daha derin çevrimler servis katmanında engellenmelidir. Recursive DB trigger/constraint V1 için gerekçesiz karmaşıklık oluşturur.

### 5.2 `units_of_measure`

**Purpose:** Malzeme miktarının ölçü birimini referans veri olarak tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Birim kimliği. |
| `code` | VARCHAR | Hayır | UNIQUE | İş kodu; UUID kararlı kimliktir, `code` yetkili yönetici tarafından düzenlenebilir (`DEC-021`). Seed örnekleri kapalı whitelist değildir. |
| `name` | VARCHAR | Hayır | Boş olamaz | Görünen ad. |
| `decimal_places` | SMALLINT | Evet | Provisional 0..3 | Birim için opsiyonel validation ipucu; kesin politika `DEC-OPEN-010`. |
| `active` | BOOLEAN | Hayır | Default true | Yeni kullanım durumu. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Yok.
- **Unique Constraints:** `code`.
- **Check Constraints:** Değer kullanılırsa `decimal_places BETWEEN 0 AND 3`; alan kesin precision kararı verilene kadar null olabilir.
- **Recommended Indexes:** Unique `code`; ek indeks gerekmez.
- **Delete Policy:** `SOFT DELETE / DEACTIVATE`; referanslı UoM deaktive edilebilir, mevcut referanslar korunur (`DEC-021`).
- **Notes / TBD:** Birim dönüşüm tablosu önerilmez. `code` değişiklikleri audit edilir; case-insensitive normalization kuralı uydurulmaz. `decimal_places` semantik olarak `DEC-OPEN-010` altında çözülmemiş kalır; rounding/conversion davranışı oluşturulmaz. Seed UoM'ler özel koruma almaz. Phase 2 catalog master data için optimistic locking zorunlu değildir (last-write-wins).

### 5.3 `material_conditions`

**Purpose:** Hareket türünden bağımsız malzeme kondisyon referans verisini tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Kondisyon kimliği. |
| `code` | VARCHAR | Hayır | UNIQUE | Kararlı iş kodu. |
| `name` | VARCHAR | Hayır | Boş olamaz | Görünen ad. |
| `active` | BOOLEAN | Hayır | Default true | Yeni işlemlerde kullanılabilirlik. |
| `sort_order` | INTEGER | Hayır | `>= 0` | Görüntüleme sırası. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Yok.
- **Unique Constraints:** `code`.
- **Check Constraints:** `sort_order >= 0`.
- **Recommended Indexes:** Unique `code`; `active, sort_order` yalnızca listeleme ihtiyacı doğrulanırsa.
- **Delete Policy:** `SOFT DELETE / DEACTIVATE`.
- **Notes / TBD:** Referans tablo, DB enum'a göre isim/sıra/aktiflik ve kontrollü gelecek genişlemesi sağlar. Başlangıç kodları seed varsayılanlarıdır; kapalı whitelist değildir. Condition label/reference metadata gelecekte dinamik olabilir; availability effect ve allowed transition'lar code/policy controlled kalır (`DEC-021`). Movement semantic type'lar code-controlled kalır. Kondisyonun kullanılabilir stoğa etkisi TBD'dir.

### 5.4 `materials`

**Purpose:** Malzeme/ürün tanımını tutar; fiziksel serialized örnek veya yetkili stok miktarı değildir.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Malzeme kimliği. |
| `material_code` | VARCHAR | Hayır | Benzersizlik TBD | İş/malzeme kodu. |
| `name` | VARCHAR | Hayır | Boş olamaz | Malzeme adı. |
| `category_id` | UUID | Hayır | FK | Malzeme kategorisi. |
| `brand` | VARCHAR | Evet | — | Marka. |
| `model` | VARCHAR | Evet | — | Model. |
| `unit_id` | UUID | Evet | FK; QUANTITY için zorunlu | Temel ölçü birimi; Phase 5.3 serialized slice'ında varsa yalnız katalog metadata'sıdır (`DEC-032`). |
| `tracking_mode` | VARCHAR | Hayır | CHECK | `QUANTITY` veya `SERIALIZED`. |
| `minimum_stock_value` | NUMERIC(18,3) | Evet | `>= 0` | Basit malzeme geneli eşik; kapsam TBD. |
| `technical_specs` | JSONB | Hayır | Default empty object | Esnek teknik nitelikler; unrestricted raw JSON editor olarak expose edilmez (`DEC-021`). |
| `active` | BOOLEAN | Hayır | Default true | Yeni işlemlerde kullanılabilirlik. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `category_id → categories.id`; nullable `unit_id → units_of_measure.id`; delete restricted.
- **Unique Constraints:** `material_code` için global unique tercih edilir, ancak Excel analizi ve iş doğrulaması olmadan kesinleştirilmez.
- **Check Constraints:** `tracking_mode IN (QUANTITY, SERIALIZED)`; `minimum_stock_value IS NULL OR minimum_stock_value >= 0`; `technical_specs` JSON object olmalıdır.
- **Recommended Indexes:** `material_code`, `name`, `category_id`, `active`. GIN yalnızca gerçek JSONB arama ihtiyacı ölçüldüğünde.
- **Delete Policy:** Referans varsa `SOFT DELETE / DEACTIVATE`; history sonrası hard delete yok.
- **Notes / TBD:** `stock_quantity` alanı kesinlikle yoktur. Bir material herhangi bir inventory ledger history'ye sahip olduktan sonra `tracking_mode` normal uygulama yollarında immutable'dır (`DEC-013`). Exceptional veri dönüşümü ayrı iş kararı ve migration projesidir. Material code benzersizliği ve kategori teknik şemaları TBD'dir. Kategori-özel teknik alanlar controlled `TechnicalFieldDefinition`-style metadata ile yönetilir (`DEC-021`, `DEC-OPEN-019`).

## 6. Location Tabloları

### 6.1 `locations`

**Purpose:** Raf/bin seviyesine kadar genişleyebilen hiyerarşik fiziksel stok yerlerini tutar. Phase 3.1 onaylı minimal şekil (`DEC-023`).

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Kalıcı lokasyon kimliği. |
| `code` | VARCHAR | Hayır | UNIQUE, case-sensitive | İş lokasyon kodu; UUID kalıcı kimliktir, `code` editable'dır. |
| `name` | VARCHAR | Hayır | Boş olamaz; unique değil | Görünen ad; editable. |
| `parent_id` | UUID | Evet | Self FK | Üst lokasyon; root'ta null. |
| `active` | BOOLEAN | Hayır | Default true | Yeni operasyonel harekete uygunluk. |
| `can_hold_stock` | BOOLEAN | Hayır | Default false; explicit capability | Lokasyonun fiziksel stok tutup tutamayacağı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `parent_id → locations.id`, delete restricted.
- **Unique Constraints:** `code` globally unique; case-sensitive. Dış boşluk trim edilir; blank yasaktır. Regex format ve forced upper/lowercase yoktur.
- **Check Constraints:** `parent_id <> id`.
- **Recommended Indexes:** `parent_id`, unique `code`, `name`, `active`; stok seçicileri için ihtiyaç doğrulanırsa `(active, can_hold_stock)`.
- **Delete Policy:** Hard delete iş operasyonu yoktur; `SOFT DELETE / DEACTIVATE`. FK'ler restricted.
- **Notes:** `location_type` Phase 3.1'de yoktur; `WAREHOUSE`/`WORKSHOP`/`SHELF`/`BIN` sabit enum implement edilmez. Hiyerarşi dinamik recursive parent ile arbitrary depth'tir; path/depth cache persist edilmez; generic tree framework yoktur. Self-parent ve descendant/cycle parenting servis katmanında engellenir. Parent sonradan değiştirilebilir. Parent status children'a cascade etmez; inactive parent'ın active child'ı olabilir. `can_hold_stock` leaf/child/name/depth/type'tan türetilmez; parent ve child bağımsız `True` olabilir. Yeni fiziksel stok yalnız `active=true AND can_hold_stock=true` lokasyona bağlanabilir; sonraki pasifleştirme historical FK'leri geçersiz yapmaz. Yetkili envanter varken non-zero stock pasifleştirme ve `can_hold_stock` True→False yasaktır (`DEC-023`); inventory entegrasyonu bunu otoritatif uygular. Tahmini fabrika Location satırları seed edilmez.

## 6A. ProductionLine Tabloları (inventory app)

Module owner: `inventory`. Location hiyerarşisinden bağımsız ayrı domain yapısıdır (`DEC-025`). ProductionLine foundation Phase 3.3'te implement edilmiştir. Quantity ISSUE first slice Phase 4.2A–4.2C'de tamamlanmıştır.

### 6A.1 `production_lines`

**Purpose:** Üretim hattı / fabrika operasyonel bağlamını dinamik master data olarak tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Kalıcı ProductionLine kimliği. |
| `code` | VARCHAR | Hayır | UNIQUE, case-sensitive | İş kodu; UUID kalıcı kimliktir, `code` editable'dır. |
| `name` | VARCHAR | Hayır | Boş olamaz; unique değil | Görünen ad; editable. |
| `parent_id` | UUID | Evet | Self FK | Üst hat/bölüm; root'ta null. |
| `active` | BOOLEAN | Hayır | Default true | Yeni ISSUE seçimine uygunluk. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `parent_id → production_lines.id`, delete restricted.
- **Unique Constraints:** `code` globally unique; case-sensitive; outer trim; blank yasak; regex/forced case yok.
- **Check Constraints:** `parent_id <> id`.
- **Recommended Indexes:** `parent_id`, unique `code`, `name`, `active`.
- **Delete Policy:** Hard delete application surface yok; `SOFT DELETE / DEACTIVATE`. Status children'a cascade etmez.
- **Notes:** Hiyerarşi dinamik recursive parent ile arbitrary depth'tir; fixed type enum yok; path/depth cache yok; generic tree framework yoktur. Self-parent ve cycle servis katmanında engellenir. Inactive ProductionLine yeni ISSUE seçiminde kullanılamaz. Gelecek ISSUE `production_line_id` + code/name snapshot taşır. Tahmin edilmiş fabrika hatları seed edilmez. Planlanan permissions: `inventory.view_productionline`, `inventory.add_productionline`, `inventory.change_productionline`.

## 7. Serialized Asset Tabloları

### 7.1 `serialized_assets`

**Purpose:** `SERIALIZED` takip modundaki bir `Material`ın tek fiziksel örneğini ve ledger'dan türeyen güncel projection/cache alanlarını tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Tekil varlık sistem kimliği. |
| `material_id` | UUID | Hayır | FK | Ait olduğu material. |
| `internal_asset_code` | VARCHAR(64) | Hayır | UNIQUE, nonblank | Zorunlu insan-facing operasyonel varlık kodu. |
| `serial_number` | VARCHAR(255) | Evet | `(material_id, serial_number)` partial UNIQUE | Optional üretici seri numarası; boş input `NULL`. |
| `current_location_id` | UUID | `IN_STOCK` Hayır; `ISSUED` Evet | FK + DB guard | `IN_STOCK` için active stock-holding lokasyon; `ISSUED` iken `NULL`. |
| `current_condition_id` | UUID | Hayır | FK + DB guard | Ledger'dan türetilen active kondisyon; ISSUE sonrası da korunur. |
| `current_state` | VARCHAR(16) | Hayır | `IN_STOCK` \| `ISSUED` | V1 projection vocabulary (`DEC-035`). |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma zamanı. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Projection/master son değişiklik zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `material_id → materials.id`; `current_location_id → locations.id`; `current_condition_id → material_conditions.id`; delete restricted.
- **Unique Constraints:** `internal_asset_code` global unique; non-null `serial_number` aynı material içinde unique, farklı material'larda tekrar kullanılabilir.
- **Check Constraints / Guards:** `internal_asset_code` nonblank ve outer-trimmed persisted identity; `serial_number` null veya outer-trimmed nonblank; `inventory_asset_state_shape`: `IN_STOCK` ⇒ location+condition NOT NULL, `ISSUED` ⇒ location NULL + condition NOT NULL; material `SERIALIZED`; IN_STOCK current location active + stock-holding; current condition active. Phase 5.4D-B, ikinci authoritative writer (baseline candidate promotion) için bu trim/nonblank DB integrity'yi kapatır; case-insensitive uniqueness yoktur. Custody/current-holder kolonu yoktur.
- **Recommended Indexes:** `material_id`, unique `internal_asset_code`, `serial_number`, `current_location_id`, `current_condition_id`.
- **Delete Policy:** Ledger history sonrası `IMMUTABLE IDENTITY / NO HARD DELETE`; Phase 5.3 generic rename/material/serial mutation workflow'u yoktur.
- **Notes:** `DEC-032` identity + `DEC-035` V1 states: güncel state/lokasyon/kondisyon projection olarak persisted tutulur. Genesis serialized RECEIVE veya scoped `INITIAL_BALANCE` ile atomik oluşur; ISSUE `ISSUED`/location NULL, linked unused RETURN `IN_STOCK`/explicit target, TRANSFER yalnız `IN_STOCK` location değişimi üretir. Ledger her zaman yetkilidir; projection read-only verifier `asset_event_seq` causal order ile ledger history reducer kullanır. Alıcı/üretim hattı/kullanım yeri `IssueContext`tedir. Serialized correction ve custody deferred'dır.

## 8. Inventory Ledger Tabloları

### 8.1 `inventory_transactions`

**Purpose:** Tamamlanmış envanter değişikliklerinin immutable/auditable ledger başlığıdır.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Ledger işlem kimliği. |
| `operation_id` | UUID | Hayır | UNIQUE | Güvenli tekrar/idempotency anahtarı. |
| `request_fingerprint` | CHAR(64) | Hayır | Server-generated SHA-256 | Canonical semantic command payload özeti. |
| `transaction_number` | VARCHAR | Evet | UNIQUE öneri | İnsan okunur referans; gereksinimi TBD. |
| `transaction_type` | VARCHAR | Hayır | CHECK | Onaylı ledger olay türü. |
| `occurred_at` | TIMESTAMPTZ | Hayır | Sistem zamanı, immutable | İşlem zamanı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Ledger'a kayıt zamanı. |
| `acting_user_id` | Auth user PK tipi | Hayır | FK | İşlemi yapan uygulama kullanıcısı. |
| `source_transaction_id` | UUID | Evet | Self FK | İade/düzeltme gibi ilişkili kaynak işlem. |
| `notes` | TEXT | Evet | — | İş notu; zorunluluk yok. |

- **Primary Key:** `id`
- **Foreign Keys:** `acting_user_id → auth user`; `source_transaction_id → inventory_transactions.id`. Correction ve baseline sonuç ilişkileri downstream workflow tabloları/linkleri tarafından sahiplenilir; inventory downstream app'lere reverse FK taşımaz.
- **Unique Constraints:** `operation_id`; opsiyonel `transaction_number`.
- **Current Phase 5.4D-B Check Constraint:** `transaction_type IN (RECEIPT, ISSUE, RETURN, TRANSFER, CONTROLLED_CORRECTION, COUNT_RECONCILIATION, INITIAL_BALANCE)`. `INITIAL_BALANCE` yalnız controlled baseline establishment tarafından oluşturulur.
- **Recommended Indexes:** `occurred_at`, `transaction_type, occurred_at`, `acting_user_id, occurred_at`, `source_transaction_id`; unique `operation_id`. Fingerprint tek başına lookup anahtarı değildir.
- **Delete Policy:** `IMMUTABLE / NO DELETE`; PostgreSQL DB-level immutability guard zorunludur.
- **Notes / TBD:** Ledger yalnızca tamamlanmış işlemleri içerdiği için mutable `status` alanı önerilmez. Taslak/validasyon import veya istek bağlamında tutulur. Committed header/line `UPDATE` ve `DELETE`, daha sonra Django migration ile yönetilecek PostgreSQL trigger-class guard tarafından reddedilmelidir; application/admin/ORM/raw application SQL bypass edemez. Exceptional repair/migration privileged, documented ve audited'dir. `INITIAL_BALANCE`, yalnızca onaylı `InventoryBaseline` üzerinden reconciled başlangıç stoğunu ledger'a alan kontrollü olaydır; serbest doğrudan stok yazımı değildir.

### 8.2 `inventory_transaction_lines`

**Purpose:** Bir ledger işleminin quantity veya tekil varlık bazındaki stok etkisini tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Satır kimliği. |
| `transaction_id` | UUID | Hayır | FK | Ledger başlığı. |
| `line_number` | INTEGER | Hayır | `> 0`, işlem içinde unique | Kararlı satır sırası. |
| `material_id` | UUID | Hayır | FK | Etkilenen material. |
| `serialized_asset_id` | UUID | Evet | FK | Serialized satırda tam bir asset. |
| `quantity` | NUMERIC(18,3) | Evet | `> 0` quantity satırda | Pozitif hareket büyüklüğü; yön type/lokasyon semantiğinden gelir. |
| `unit_id` | UUID | Evet | FK | Quantity satırda zorunlu, serialized satırda null. |
| `condition_id` | UUID | Hayır | FK | Hareketten ayrı kondisyon. |
| `source_location_id` | UUID | Evet | FK | Kaynak fiziksel lokasyon. |
| `target_location_id` | UUID | Evet | FK | Hedef fiziksel lokasyon. |
| `original_issue_line_id` | UUID | Evet | self-FK, RESTRICT | Yalnız RETURN line için zorunlu immutable original ISSUE lineage'i (`DEC-028`). |
| `corrected_line_id` | UUID | Evet | self-FK, RESTRICT | Yalnız CONTROLLED_CORRECTION line için zorunlu canonical original-line lineage (`DEC-030`). |
| `asset_event_seq` | INTEGER | Evet | serialized satırda `>= 1` | Serialized satırın asset-scope nedensel ledger sırası. Quantity satırda `NULL`. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Immutable satır kayıt zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Başlık, material, asset, unit, condition, source/target location, nullable `original_issue_line_id` ve `corrected_line_id → inventory_transaction_lines.id`; tamamı historical delete restricted.
- **Unique Constraints:** `(transaction_id, line_number)`; non-null asset için `(transaction_id, serialized_asset_id)` koşullu unique; serialized satır için `(serialized_asset_id, asset_event_seq)` koşullu unique.
- **Check Constraints:** Ya `(serialized_asset_id IS NULL AND quantity > 0 AND unit_id IS NOT NULL)` ya da `(serialized_asset_id IS NOT NULL AND quantity IS NULL AND unit_id IS NULL)`; ikisi birlikte olamaz. `(source_location_id IS NULL OR target_location_id IS NULL OR source_location_id <> target_location_id)`. Quantity satırda `asset_event_seq IS NULL`; serialized satırda `asset_event_seq >= 1`.
- **Recommended Indexes:** `transaction_id`, `material_id`, `serialized_asset_id`, `source_location_id`, `target_location_id`, `condition_id`, `original_issue_line_id`, `corrected_line_id`.
- **Delete Policy:** `IMMUTABLE / NO DELETE`.
- **Notes:** `DEC-032`/`DEC-035`: serialized satırda `quantity` ve `unit` kesin olarak `NULL`, asset zorunludur; `1 adet` saklanmaz. Genesis: source-null/target-required serialized RECEIVE veya `INITIAL_BALANCE`; asset başına exactly one establishing genesis. Serialized ISSUE: source required, target NULL, IssueContext required. Serialized RETURN: source NULL, target required, `original_issue_line` serialized ISSUE, same asset/material/condition, unique lineage. Serialized TRANSFER: source+target required ve farklı, lineage/IssueContext yok. Line material = asset material; parent-aware PostgreSQL guard fail-closed kalır. `asset_event_seq` SerializedAsset satır kilidi altında `max(existing)+1` (genesis=1) atanır; verifier `serialized_asset_id → asset_event_seq` ile replay eder ve wall-clock/`occurred_at`/`created_at`/UUID sıralamasına güvenmez. Fingerprint'e girmez. Serialized correction deferred'dır. Unique `inventory_serialized_return_original_uniq` bir serialized ISSUE line'ın ikinci RETURN'ünü reddeder.

## 9. Stock Balance Projection

### 9.1 `stock_balances`

**Purpose:** Yalnızca quantity malzemeler için hızlı, güncel stok projection'ı/cache'idir; historical truth değildir.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Projection satır kimliği. |
| `material_id` | UUID | Hayır | FK | `QUANTITY` material. |
| `location_id` | UUID | Hayır | FK | Fiziksel stok lokasyonu. |
| `condition_id` | UUID | Hayır | FK | Kondisyon partition'ı. |
| `quantity` | NUMERIC(18,3) | Hayır | `>= 0` | Güncel miktar. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son projection güncellemesi. |

- **Primary Key:** `id`
- **Foreign Keys:** `material_id → materials.id`; `location_id → locations.id`; `condition_id → material_conditions.id`; delete restricted.
- **Unique Constraints:** `(material_id, location_id, condition_id)`.
- **Check Constraints:** `quantity >= 0`.
- **Recommended Indexes:** Composite unique anahtar; `location_id`; düşük stok sorguları doğrulanınca ek indeks değerlendirilir.
- **Delete Policy:** `SYSTEM-REBUILDABLE PROJECTION`; kullanıcı hard delete/direkt edit yapamaz. Ledger'dan kontrollü yeniden üretilebilir.
- **Notes / TBD:** Yalnızca `QUANTITY` tracking mode ve yalnız `active && can_hold_stock` location kabulü serviste zorunlu doğrulanır; catastrophic cross-table ihlal için targeted PostgreSQL DB integrity guard/constraint trigger gerekir. Değişiklikler ledger yazımıyla aynı DB transaction içinde pessimistic row lock altında yapılır. Kullanılmayan optimistic `version` kolonu tutulmaz. Var olmayan row için lock tek başına yeterli değildir; Bölüm 22'deki unique + conflict + lock contract uygulanır.

## 10. Issue Context

### 10.1 `issue_contexts`

**Purpose:** Yalnızca `ISSUE` transaction'a ait zorunlu alıcı ve kullanım bağlamını bir-bir tabloda tarihsel snapshot olarak tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `transaction_id` | UUID | Hayır | PK, FK, UNIQUE | İlgili `ISSUE` transaction. |
| `receiver_employee_id` | UUID | Hayır | FK | Teslim alan Employee UUID referansı (`DEC-024`). |
| `receiver_first_name_snapshot` | VARCHAR | Hayır | Boş olamaz | Teslim anındaki ad. |
| `receiver_last_name_snapshot` | VARCHAR | Hayır | Boş olamaz | Teslim anındaki soyad. |
| `receiver_employee_number_snapshot` | VARCHAR | Hayır | Boş olamaz | Teslim anındaki sicil no. |
| `production_line_id` | UUID | Hayır | FK | Seçilen ProductionLine UUID referansı (`DEC-025`). |
| `production_line_code_snapshot` | VARCHAR | Hayır | Boş olamaz | Teslim anındaki hat kodu. |
| `production_line_name_snapshot` | VARCHAR | Hayır | Boş olamaz | Teslim anındaki hat adı. |
| `usage_location_text` | VARCHAR | Hayır | Boş olamaz | Zorunlu fiili kullanım yeri (exact usage place; ayrı free text). |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Snapshot kayıt zamanı. |

- **Primary Key:** `transaction_id`
- **Foreign Keys:** `transaction_id → inventory_transactions.id`; `receiver_employee_id → employees.id`; `production_line_id → production_lines.id`; delete restricted.
- **Unique Constraints:** PK transaction başına en fazla bir issue context sağlar.
- **Check Constraints:** Snapshot ve iki kullanım alanı normalize edildikten sonra boş olamaz.
- **Recommended Indexes:** `receiver_employee_id`, gerekirse `receiver_employee_number_snapshot`; transaction PK indeksi yeterlidir.
- **Delete Policy:** İlgili transaction gibi `IMMUTABLE / NO DELETE`.
- **Notes / TBD:** Ayrı tablo; ISSUE dışı işlemlerde gereksiz nullable kolonları önler. `transaction_type = ISSUE`, her ISSUE için context completeness ve persisted context immutability Phase 4.2A PostgreSQL guards ile uygulanmıştır; RETURN IssueContext sahiplenemez. ProductionLine foundation `DEC-025`, Employee foundation `DEC-024`, quantity ISSUE contract `DEC-027` ile kararlıdır. `usage_location_text` exact usage place olarak ayrı required free text kalır; `UsagePlace` modeli yoktur; Location veya ProductionLine'dan infer edilmez.

## 11. Correction Tabloları

### 11.1 `correction_requests`

**Purpose:** Tamamlanmış ledger işlemi için kontrollü düzeltme talebi ve kararını tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Talep kimliği. |
| `original_transaction_id` | UUID | Hayır | FK | Düzeltilecek özgün ledger işlemi. |
| `original_line_id` | UUID | Hayır | FK | Transaction'a ait canonical özgün quantity satırı. |
| `original_location_id` | UUID | Hayır | FK | Özgün line'ın source/target konumlarından düzeltilecek bucket. |
| `requester_id` | Auth user PK tipi | Hayır | FK | Talep eden kullanıcı. |
| `explanation` | TEXT | Hayır | Trim edilmiş 10..2000 | Zorunlu açıklama. |
| `effect_type` | VARCHAR | Hayır | CHECK | `QUANTITY` veya `IDENTITY`. |
| `quantity_effect` | NUMERIC(18,3) | Hayır | Effect-shape check | QUANTITY için signed/non-zero; IDENTITY için positive restatement miktarı. |
| `corrected_material_id` | UUID | Evet | FK | IDENTITY için doğru material. |
| `corrected_location_id` | UUID | Evet | FK | IDENTITY için doğru stock-holding location. |
| `corrected_condition_id` | UUID | Evet | FK | IDENTITY için doğru condition. |
| `status` | VARCHAR | Hayır | CHECK | `PENDING`, `APPROVED`, `REJECTED`. |
| `requested_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Talep zamanı. |
| `decided_by_id` | Auth user PK tipi | Evet | FK | Karar veren yetkili kullanıcı. |
| `decided_at` | TIMESTAMPTZ | Evet | — | Sistem karar zamanı. |
| `rejection_reason` | TEXT | Evet | PROPOSED | Ret gerekçesi. |
| `resulting_transaction_id` | UUID | Evet | One-to-one FK | APPROVED ise `CONTROLLED_CORRECTION` sonucu; ilişki corrections tarafında. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Karar öncesi/karar güncelleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Özgün transaction/line/location, corrected identity, iki auth user ve workflow-owned result transaction FK'leri; delete restricted.
- **Unique Constraints:** `(original_transaction_id) WHERE status = PENDING`; result transaction one-to-one. Terminal sonrası yeni request açılabilir.
- **Check/trigger constraints:** Status/decision/result completeness; effect shape; trimmed explanation; original line membership ve non-correction QUANTITY root; original bucket membership; requester != decider; result type/lineage; submitted semantic fields immutable; no hard delete. Ret gerekçesi DB'de zorunlu yapılmaz.
- **Recommended Indexes:** `status, requested_at`, `original_transaction_id`, `requested_by_user_id`, `decided_by_user_id`.
- **Delete Policy:** Gönderim sonrası `IMMUTABLE / NO HARD DELETE`; yalnızca kontrollü durum geçişi.
- **Notes:** `DEC-030` quantity first slice kararlıdır. Sonuç transaction ilişkisinin sahibi correction workflow'dur; ledger üzerinde reverse `correction_request_id` yoktur. Pure quantity effect bir correction line, identity restatement iki correction line üretir. `rejection_reason` zorunluluğu yalnızca PROPOSED'dır. Serialized/non-stock correction deferred kalır. `DEC-034` yeni talep için evidence'ı service katmanında zorunlu kılar; historical satırlar için DB-level "her request'in evidence'ı vardır" kuralı yoktur.

## 12. Correction Evidence Tabloları

### 12.1 `correction_evidence`

**Purpose:** Düzeltme talebinin photographic evidence metadata'sını tutar. Binary PostgreSQL'de tutulmaz. Phase 5.5 / `DEC-034` ile uygulanmıştır.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Kanıt metadata kimliği. |
| `correction_request_id` | UUID | Hayır | FK | Sahibi olan düzeltme talebi. |
| `original_filename` | VARCHAR | Hayır | Boş olamaz | Kullanıcı dosya adının path-stripped snapshot'ı. |
| `storage_key` | VARCHAR | Hayır | UNIQUE | Private storage anahtarı (`corrections/evidence/<uuid>.<ext>`). |
| `content_type` | VARCHAR | Hayır | `image/jpeg`, `image/png`, `image/webp` | Tespit edilen MIME türü. |
| `size_bytes` | BIGINT | Hayır | `> 0`, ≤ 10 MiB (service) | Dosya boyutu. |
| `sha256` | CHAR(64) | Hayır | hex digest | İçerik SHA-256. |
| `uploaded_by_id` | Auth user PK tipi | Hayır | FK | Yükleyen kullanıcı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Yükleme zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `correction_request_id → correction_requests.id`; `uploaded_by_id → auth user`; delete restricted.
- **Unique Constraints:** `storage_key`.
- **Check Constraints:** `size_bytes > 0`; supported content types; SHA-256 hex; safe storage key prefix.
- **Recommended Indexes:** `correction_request_id, created_at`.
- **Delete Policy:** `IMMUTABLE / NO HARD DELETE`. Ordinary UPDATE/DELETE PostgreSQL guard ile kesilir. V1 otomatik retention silme yoktur.
- **Notes:** Metadata ownership `corrections`; binary `core.storage` private filesystem (MEDIA_URL dışında). Sensitive evidence public `MEDIA_URL` ile değil `/corrections/evidence/<uuid>/` authenticated path ile servis edilir. Yeni request için en az bir satır service katmanında zorunludur; historical pre-Phase-5.5 `CorrectionRequest` satırları evidence'siz kalabilir. HEIC/HEIF/GIF/SVG/PDF reddedilir. File-before-DB (`DEC-018`); DB rollback orphan file bırakabilir, V1 otomatik cleanup yoktur.

## 13. Physical Count Tabloları

Tek tabloda quantity ve serialized alanları tutmak çok sayıda nullable kolon ve kırılgan çapraz check üretir. İki uzmanlaşmış satır tablosu önerilir.

### 13.1 `physical_count_sessions`

**Purpose:** Belirli kapsamda fiziksel sayım ve mutabakat oturumunu tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Oturum kimliği. |
| `reference_number` | VARCHAR | Hayır | UNIQUE | İnsan okunur sayım referansı. |
| `scope_location_id` | UUID | Hayır | FK | Sayım kapsamındaki tek `Location` subtree'sinin kökü. |
| `status` | VARCHAR | Hayır | `DRAFT`, `STARTED`, `COMPLETED` | Oturum yaşam döngüsü. |
| `reconciliation_status` | VARCHAR | Hayır | `NOT_STARTED`, `PENDING`, `COMPLETED` | Fark çözüm durumu. |
| `started_by_user_id` | Auth user PK tipi | Evet | FK; `STARTED`/`COMPLETED` için zorunlu | Başlatan kullanıcı. |
| `started_at` | TIMESTAMPTZ | Evet | `STARTED`/`COMPLETED` için sistem zamanı | Başlangıç. |
| `completed_by_user_id` | Auth user PK tipi | Evet | FK | Tamamlayan kullanıcı. |
| `completed_at` | TIMESTAMPTZ | Evet | — | Tamamlama zamanı. |
| `baseline_candidate` | BOOLEAN | Hayır | Default false | Cutover adayı olup olmadığı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son yaşam döngüsü güncellemesi. |

- **Primary Key:** `id`
- **Foreign Keys:** Scope location ve auth user FK'leri; delete restricted.
- **Unique Constraints:** `reference_number`.
- **Check Constraints:** `DRAFT` için start/complete metadata null; `STARTED` için start actor/time dolu ve complete metadata null; `COMPLETED` için start/complete actor/time dolu; status ve reconciliation status controlled vocabulary içinde; `DRAFT`/`STARTED` yalnız `NOT_STARTED` reconciliation, `COMPLETED` yalnız `PENDING`/`COMPLETED`.
- **Recommended Indexes:** `status`, `reconciliation_status`, `scope_location_id`, `started_at`.
- **Delete Policy:** Başlatıldıktan sonra `RETAIN / NO HARD DELETE`.
- **Notes:** `DEC-033` stock-stability kararını kapatır: freeze yoktur; Phase 5.4B'deki atomik start adımı session'ı `DRAFT`tan `STARTED`a geçirirken tek Location subtree için immutable expected snapshot oluşturur. Çakışan subtree'lerde çakışan açık session yasaktır. Reconciliation idempotent/double-apply korumalıdır ve `lock → re-read → drift check → revalidate → write` sırasını izler; drift varsa reddedilip recount/reconfirmation istenir. `baseline_candidate=false` routine, `true` cutover session'dır. `STARTED` sonrasında scope, baseline sınıflandırması, start metadata ve expected snapshot ordinary write yollarında immutable'dır.

### 13.2 `physical_count_quantity_lines`

**Purpose:** Quantity material için beklenen ve fiziksel sayılan miktarı karşılaştırır.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Sayım satırı kimliği. |
| `session_id` | UUID | Hayır | FK | Sayım oturumu. |
| `material_id` | UUID | Hayır | FK | `QUANTITY` material. |
| `location_id` | UUID | Hayır | FK | Sayılan lokasyon. |
| `condition_id` | UUID | Hayır | FK | Sayılan kondisyon partition'ı. |
| `expected_quantity` | NUMERIC(18,3) | Hayır | `>= 0` | Sayım başlangıcındaki sistem snapshot'ı. |
| `counted_quantity` | NUMERIC(18,3) | Evet | `>= 0` | Fiziksel sonuç; sayılana kadar null. |
| `counted_by_user_id` | Auth user PK tipi | Evet | FK | Sayımı yapan. |
| `counted_at` | TIMESTAMPTZ | Evet | — | Sayım zamanı. |
| `resolution_status` | VARCHAR | Hayır | Controlled value | Pending-count / explicit-not-counted / no-discrepancy / pending-approval / approved / dispositioned durumunu ayırır. |
| `approved_by_user_id` | Auth user PK tipi | Evet | FK | Sıfır olmayan routine discrepancy'yi onaylayan kullanıcı. |
| `approved_at` | TIMESTAMPTZ | Evet | APPROVED iken zorunlu | Onay zamanı. |
| `approval_explanation` | TEXT | Evet | Trim 10..2000 when approved | Explicit approval gerekçesi. |
| `reconciliation_transaction_id` | UUID | Evet | FK | Counting-owned dedicated `COUNT_RECONCILIATION` sonucu; cutover session'da null. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Satır oluşturma zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Session, material, location, condition, counted/approved user ve optional result inventory transaction FK'leri.
- **Unique Constraints:** `(session_id, material_id, location_id, condition_id)`.
- **Check Constraints:** Beklenen miktar negatif değil; sayılmışsa miktar negatif değil ve counted user/time birlikte dolu; APPROVED metadata/shape ve self-approval yasağı.
- **Recommended Indexes:** Composite unique anahtar; `material_id`, `location_id`, `resolution_status`, optional result transaction.
- **Delete Policy:** `DRAFT` session satırları başlangıç öncesi kaldırılabilir; `STARTED`/`COMPLETED` session snapshot geçmişi `RETAIN / NO HARD DELETE`tir.
- **Notes:** `PENDING_COUNT`, actor/time olmadan henüz işlenmemiş satırdır. Explicit `NOT_COUNTED`, `counted_quantity=NULL` bırakır ancak kullanıcı eylemini `counted_by_user` ve `counted_at` ile saklar; fiziksel explicit zero ise NULL değildir ve normal counted state'tir. Routine session yalnız bütün required expected satırlar fiziksel sayılmış veya explicit `NOT_COUNTED` disposition almışsa fiziksel sayımı tamamlayabilir. `baseline_candidate=true` session'da required satırlar fiziksel sayılmalıdır; hem `PENDING_COUNT` hem explicit `NOT_COUNTED` completion'ı engeller. Fark `counted_quantity - expected_quantity` olarak türetilir; ayrıca mutable kolon önerilmez. Expected değer session-start immutable snapshot'ıdır; zero-quantity `StockBalance` row'ları snapshot'a alınmaz. Existing QUANTITY Material + condition için unexpected physical stock `expected_quantity=0` satırı eklenebilir. Missing row zero değildir. Phase 5.4B start transaction'ı `PhysicalCountSession → Material → Location → MaterialCondition → StockBalance` kilit sırasını izler; bütün QUANTITY Material satırlarını ve Location ağacını kısa süreli sabitleyip projection satırlarını tek authoritative snapshot statement'ında okur. Routine approved positive effect target-only, negative effect source-only `COUNT_RECONCILIATION` line'ıdır. `CorrectionRequest` FK'si yoktur. APPROVED satırın expected/counted temel, bucket kimliği ve result link'i ordinary UPDATE/DELETE ile değiştirilemez.

### 13.2.1 `physical_count_quantity_rejections`

**Purpose:** Routine QUANTITY discrepancy ret kaydını stok etkisi olmadan saklar ve recount için oturumu yeniden açar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Ret kaydı kimliği. |
| `line_id` | UUID | Hayır | FK | Reddedilen sayım satırı. |
| `rejected_by_user_id` | Auth user PK tipi | Hayır | FK | Karar aktörü. |
| `rejected_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Ret zamanı. |
| `reason` | TEXT | Evet | Trim; boş string NULL | İsteğe bağlı ret nedeni; zorunlu değildir. |
| `counted_quantity` | NUMERIC(18,3) | Hayır | `>= 0` | Reddedilen fiziksel sayım snapshot'ı. |
| `counted_by_user_id` | Auth user PK tipi | Hayır | FK | Reddedilen sayım aktörü. |
| `counted_at` | TIMESTAMPTZ | Hayır | — | Reddedilen sayım zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Line ve auth user FK'leri; delete restricted.
- **Delete Policy:** `IMMUTABLE / NO DELETE`; PostgreSQL trigger guard.
- **Notes:** Ret ledger veya `StockBalance` yazmaz; expected snapshot'ı rewrite etmez. Oturum `STARTED` + `NOT_STARTED` durumuna döner ve explicit recount CAS token'ı mevcut `counted_at` ile devam eder. Permission rollout Phase 5.4E'de tamamlanmıştır. Quantity discrepancy review/approve/reject UI review için uygulanmıştır; COMPLETE işaretlenmez.

### 13.3 `physical_count_asset_lines`

**Purpose:** Serialized varlığın beklenen ve fiziksel var/yok durumunu karşılaştırır.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Sayım satırı kimliği. |
| `session_id` | UUID | Hayır | FK | Sayım oturumu. |
| `serialized_asset_id` | UUID | Hayır | FK | Sayılan tekil varlık. |
| `location_id` | UUID | Hayır | FK | Kontrol edilen fiziksel lokasyon. |
| `expected_present` | BOOLEAN | Hayır | — | Sistem snapshot'ında beklenme. |
| `counted_present` | BOOLEAN | Evet | — | Fiziksel sonuç; sayılana kadar null. |
| `expected_condition_id` | UUID | Evet | FK | Sayım başlangıcındaki kondisyon snapshot referansı. |
| `observed_condition_id` | UUID | Evet | FK | Fiziksel gözlem kondisyonu. |
| `counted_by_user_id` | Auth user PK tipi | Evet | FK | Sayımı yapan. |
| `counted_at` | TIMESTAMPTZ | Evet | — | Sayım zamanı. |
| `resolution_status` | VARCHAR | Hayır | Controlled value | Not-counted / matched / missing / unexpected / dispositioned durumunu ayırır. |
| `approved_by_user_id` | Auth user PK tipi | Evet | FK | Gerekli serialized discrepancy karar aktörü. |
| `approval_explanation` | TEXT | Evet | Trim 10..2000 when approved | Explicit approval/disposition gerekçesi. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Satır oluşturma zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Session, asset, location, beklenen/gözlenen condition ve counted/approved user FK'leri.
- **Unique Constraints:** `(session_id, serialized_asset_id, location_id)`.
- **Check Constraints:** Sayılmışsa counted user/time birlikte dolu.
- **Recommended Indexes:** Composite unique anahtar; `serialized_asset_id`, `location_id`, `resolution_status`.
- **Delete Policy:** `RETAIN / NO HARD DELETE`.
- **Notes:** Beklenmeyen bulunan known asset `expected_present=false, counted_present=true`; eksik asset tersiyle temsil edilir. `expected_condition_id`, sayım sonrasında asset projection'ı değişse bile başlangıç karşılaştırmasını korur. Serialized count Phase 5.3 UUID/internal-code/current-state modelini kullanır ve yeni lifecycle state eklemez. Unknown catalog item authoritative asset/stock oluşturamaz; resolved veya explicitly abandoned olana kadar ayrı unresolved evidence/disposition olarak kalır. Missing row zero/present=false değildir. Phase 5.4D-A counting-owned `PhysicalCountSerializedLine` uygular: expected satırlar authoritative `SerializedAsset` snapshot referansıdır; existing authoritative asset unexpected olarak gözlemlenebilir; fiziksel bulunan candidate item `serialized_asset` olmadan staging-only kaydedilir ve authoritative asset oluşturmaz. Untouched expected ≠ explicit missing. Counting establishment öncesi non-authoritative kalır; serialized `COUNT_RECONCILIATION` yoktur. Phase 5.4D-B establishment, uygun candidate satırı (`serialized_asset=NULL`) authoritative `SerializedAsset` + serialized `INITIAL_BALANCE` olarak yükseltebilir; count satırı staging evidence olarak kalır. Phase 5.3 normalizasyon sınırı 5.4D-B DB trim/nonblank guard ile kapanmıştır.

## 14. Import Tabloları

### 14.1 `import_batches`

**Purpose:** Excel kaynak dosyasının yükleme, doğrulama, ön izleme ve kontrollü commit sürecini tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Batch kimliği. |
| `source_filename` | VARCHAR | Hayır | Boş olamaz | Orijinal dosya adı. |
| `source_storage_key` | VARCHAR | Hayır | UNIQUE | Kaynak dosya saklama anahtarı. |
| `source_checksum` | VARCHAR | Evet | İndeks öneri | Kaynak bütünlük/duplicate ipucu. |
| `uploaded_by_user_id` | Auth user PK tipi | Hayır | FK | Yükleyen kullanıcı. |
| `uploaded_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Yükleme zamanı. |
| `status` | VARCHAR | Hayır | CHECK | Staging yaşam döngüsü. |
| `validation_summary` | JSONB | Hayır | Default object | Toplam hata/uyarı özeti. |
| `committed_by_user_id` | Auth user PK tipi | Evet | FK | Kontrollü commit aktörü. |
| `committed_at` | TIMESTAMPTZ | Evet | — | Commit zamanı. |
| `operation_id` | UUID | Hayır | UNIQUE | Import commit idempotency anahtarı. |
| `request_fingerprint` | CHAR(64) | Hayır | Server-generated SHA-256 | Master-data commit semantic payload özeti. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son staging güncellemesi. |

- **Primary Key:** `id`
- **Foreign Keys:** Upload/commit auth user FK'leri.
- **Unique Constraints:** `source_storage_key`, `operation_id`.
- **Check Constraints:** Committed state için committed user/time birlikte dolu; kesin status değerleri tasarım kararıdır.
- **Recommended Indexes:** `status`, `uploaded_at`, `source_checksum`.
- **Delete Policy:** Commit edilmiş batch ve referansları `RETAIN`; staging retention süresi TBD.
- **Notes / TBD:** Aday status'ler `UPLOADED`, `VALIDATING`, `INVALID`, `VALIDATED`, `COMMITTING`, `COMMITTED` olabilir; exact state implementation review'da kesinleştirilir. Commit satırı lock edilip state tekrar doğrulanır; concurrent double commit unique operation/fingerprint ve locked transition ile engellenir. Import commit yalnız controlled master-data etkisi oluşturabilir; ledger veya `StockBalance` etkisi yoktur.

### 14.2 `import_rows`

**Purpose:** Her Excel satırını yetkili tablolardan ayrı staging/doğrulama kaydı olarak tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Staging satır kimliği. |
| `import_batch_id` | UUID | Hayır | FK | Sahibi batch. |
| `source_row_number` | INTEGER | Hayır | `> 0` | Excel satır numarası. |
| `raw_data` | JSONB | Hayır | — | Kaynak satır snapshot'ı. |
| `mapped_data` | JSONB | Evet | — | Aday domain eşlemesi. |
| `validation_status` | VARCHAR | Hayır | CHECK | Satır doğrulama durumu. |
| `errors` | JSONB | Hayır | Default array | Doğrulama hataları. |
| `warnings` | JSONB | Hayır | Default array | Doğrulama uyarıları. |
| `resulting_material_id` | UUID | Evet | FK | Commit sonucu material. |
| `master_data_outcomes` | JSONB | Hayır | Default object | Oluşturulan/güncellenen kontrollü master-data referans ve outcome metadata'sı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Oluşturma. |
| `updated_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Son doğrulama güncellemesi. |

- **Primary Key:** `id`
- **Foreign Keys:** Batch ve resulting material FK'leri. Import row hiçbir stock transaction'a FK vermez.
- **Unique Constraints:** `(import_batch_id, source_row_number)`.
- **Check Constraints:** `source_row_number > 0`; errors/warnings JSON array olmalıdır.
- **Recommended Indexes:** `import_batch_id, validation_status`, `resulting_material_id`.
- **Delete Policy:** Commit edilmiş satırlar `RETAIN`; commit edilmemiş staging retention TBD.
- **Notes / TBD:** `raw_data`/`mapped_data` staging içindir; stok/history kaynağı değildir. Imported quantity karşılaştırma/count hazırlığı için `mapped_data` içinde kalabilir. Geçersiz satır yetkili tablo etkisi oluşturamaz. **CANDIDATE INVENTORY IS NOT LEDGER DATA AND IS NOT STOCK.** Gerçek açılış stoğu yalnız reconciled baseline'ın scoped `INITIAL_BALANCE` transaction'larıyla oluşturulur.

## 15. Barcode / QR Tabloları

V1'de `barcode_identifiers` tablosu yoktur (`DEC-036`). First-party makine okunur kimlik, `Material` / `SerializedAsset` / `Location` UUID'sinden compact reversible token olarak türetilir ve persist edilmez. Code128 ve QR aynı `TZ1M|A|L:<22-char-base64url-uuid>` payload metnini taşır. External/legacy barcode registry ihtiyacı ayrı karardır.

Tarihsel conceptual `BarcodeIdentifier` modeli `DEC-011` ownership sınırını kaydeder; V1 schema bunu tablo olarak uygulamaz.

## 16. Audit Tabloları

### 16.1 `audit_events`

**Purpose:** Inventory ledger'ını çoğaltmadan ana veri, rol, düzeltme kararı, import ve cutover gibi idari eylemleri denetlenebilir kılar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Audit event kimliği. |
| `event_type` | VARCHAR | Hayır | Kontrollü sözlük | İdari olay türü. |
| `actor_user_id` | Auth user PK tipi | Hayır | FK | Olay aktörü. |
| `occurred_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Olay zamanı. |
| `entity_type` | VARCHAR | Hayır | Kontrollü sözlük | Hedef entity türü. |
| `entity_id` | UUID | Hayır | Service-validated | Hedef business entity kimliği. |
| `before_data` | JSONB | Evet | — | Gerekli alanların önceki snapshot'ı. |
| `after_data` | JSONB | Evet | — | Gerekli alanların sonraki snapshot'ı. |
| `metadata` | JSONB | Hayır | Default object | İlişkili karar/import bağlamı. |

- **Primary Key:** `id`
- **Foreign Keys:** `actor_user_id → auth user`. Generic hedef için DB FK yoktur.
- **Unique Constraints:** Yok; idempotent idari olay gerekiyorsa gelecekte operation key eklenebilir.
- **Check Constraints:** `event_type` ve `entity_type` boş olamaz; JSONB alanları object olmalıdır.
- **Recommended Indexes:** `occurred_at`, `actor_user_id, occurred_at`, `(entity_type, entity_id)`, `event_type, occurred_at`.
- **Delete Policy:** `IMMUTABLE / NO DELETE`; retention süresi TBD.
- **Notes / TBD:** Typed generic reference basit ve tüm master tablolar için ayrı nullable FK'lardan daha yönetilebilirdir; DB referans bütünlüğü vermez. Servis hedefi doğrular, audit kaydı hedef silinse bile kalır. Inventory işlemleri ayrıca kopyalanmaz; ledger zaten audit kaynağıdır.

## 17. Inventory Baseline

### 17.1 `inventory_baselines`

**Purpose:** Reconciled başlangıç envanterinin kontrollü olarak yetkili kaynak hâline geldiği cutover kaydını tutar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Baseline kimliği. |
| `reference` | VARCHAR | Hayır | UNIQUE | İnsan okunur cutover referansı. |
| `import_batch_id` | UUID | Evet | FK | Staging/count-reference data'nın import kaynağı; stock authority değildir. |
| `status` | VARCHAR | Hayır | Controlled lifecycle | Hazırlık ve authoritative establishment durumu. |
| `established_by_user_id` | Auth user PK tipi | Evet | FK | Cutover işlemini tamamlayan yetkili kullanıcı. |
| `established_at` | TIMESTAMPTZ | Evet | Sistem zamanı | Yetki başlangıç zamanı. |
| `notes` | TEXT | Evet | — | Açıklama. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Kayıt zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Import batch ve auth user FK'leri; delete restricted. Count session ilişkileri Bölüm 17.2, initial transaction ilişkileri Bölüm 17.3 downstream-owned link tablolarındadır.
- **Unique Constraints:** `reference`; aynı cutover context için en fazla bir authoritative `ESTABLISHED` baseline sağlayan partial unique strateji implementation review'da kesinleştirilir.
- **Check Constraints:** `ESTABLISHED` ise established actor/time dolu, değilse ikisi null; cross-table count reconciliation ve scope-completion kuralları service + targeted DB guard'dadır.
- **Recommended Indexes:** `import_batch_id`, `established_at`.
- **Delete Policy:** `IMMUTABLE / NO DELETE`.
- **Notes:** Açık bir baseline tablosu import commit ile authority cutover'ını ayırır. `DEC-033` ile bir baseline `1..N` baseline-candidate count session kapsar ve tek authoritative pilot cutover hem QUANTITY hem SERIALIZED inventory'yi içerir. Bütün required scope'lar complete, required `not-counted` satırlar bitmiş, gerekli unresolved item'lar resolved/dispositioned, bütün opening ledger effects committed ve projection verification clean olmadan `ESTABLISHED` olamaz. Establishment sensitive permission-based capability'dir (`imports.establish_baseline`); fresh ADMIN_MANAGER şablonunda kalır, safe allowlist dışındadır. Prior authoritative inventory ledger history taşıyan bucket opening balance için uygun değildir. Phase 5.4 backend COMPLETE. Operational baseline UI review için uygulanmıştır; COMPLETE işaretlenmez.

### 17.2 `inventory_baseline_count_session_links`

**Purpose:** Bir baseline'ın `1..N` required cutover count session'ını downstream-owned ilişkiyle toplamasını sağlar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Link kimliği. |
| `inventory_baseline_id` | UUID | Hayır | FK | İlişkinin sahibi baseline. |
| `physical_count_session_id` | UUID | Hayır | FK | İlgili `baseline_candidate=true` session. |
| `required` | BOOLEAN | Hayır | Default true | Establishment önkoşuluna dahil scope. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Bağlantı zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** Baseline ve physical count session; delete restricted.
- **Unique Constraints:** `(inventory_baseline_id, physical_count_session_id)`.
- **Check Constraints:** Session'ın baseline candidate olması row-local değildir; service validation ve targeted DB guard gerektirir.
- **Delete Policy:** Baseline establishment sonrası `IMMUTABLE / NO DELETE`.

### 17.3 `inventory_baseline_transaction_links`

**Purpose:** Bir baseline'ın tek dev transaction zorunluluğu olmadan bir veya daha çok scoped `INITIAL_BALANCE` ledger işlemini downstream-owned ilişkiyle toplamasını sağlar.

| Column | Conceptual Type | Null | Constraint | Description |
|---|---|---:|---|---|
| `id` | UUID | Hayır | PK | Link kimliği. |
| `inventory_baseline_id` | UUID | Hayır | FK | İlişkinin sahibi baseline. |
| `inventory_transaction_id` | UUID | Hayır | FK, UNIQUE | İlgili `INITIAL_BALANCE` transaction. |
| `scope_key` | VARCHAR | Hayır | Baseline içinde UNIQUE | Lokasyon/sayım scope'unu kararlı biçimde tanımlayan teknik anahtar. |
| `operation_id` | UUID | Hayır | UNIQUE | Scope establishment idempotency anahtarı. |
| `created_at` | TIMESTAMPTZ | Hayır | Sistem zamanı | Bağlantı zamanı. |

- **Primary Key:** `id`
- **Foreign Keys:** `inventory_baseline_id → inventory_baselines.id`; `inventory_transaction_id → inventory_transactions.id`; delete restricted.
- **Unique Constraints:** `inventory_transaction_id`, `operation_id`, `(inventory_baseline_id, scope_key)`.
- **Check Constraints:** Transaction type'ın `INITIAL_BALANCE` olması row-local değildir; service doğrulaması ve targeted PostgreSQL constraint trigger/guard gerektirir.
- **Recommended Indexes:** `(inventory_baseline_id, scope_key)` unique; transaction/operation unique indeksleri.
- **Delete Policy:** Baseline establishment sonrası `IMMUTABLE / NO DELETE`.
- **Notes / TBD:** `scope_key` iş hiyerarşisi uydurmaz; seçilen count/cutover scope'unun stable teknik kimliğidir. Inventory ledger bu linke veya baseline'a reverse FK taşımaz.

## 18. Relationship Matrix

| Parent / Source | Child / Target | Cardinality | FK / Not |
|---|---|---|---|
| `employees` | Django auth user | 0..1 : 0..1 | `employees.user_id` nullable one-to-one; ownership Employee; delete `SET_NULL` (`DEC-024`). |
| `production_lines` | child `production_lines` | 1 : 0..N | `parent_id`; Location'dan bağımsız (`DEC-025`). |
| `categories` | child `categories` | 1 : 0..N | `parent_id`. |
| `categories` | `materials` | 1 : 0..N | Her material bir category'ye bağlı. |
| `units_of_measure` | `materials` | 1 : 0..N | QUANTITY material için temel unit zorunlu; Phase 5.3 serialized material için varsa yalnız katalog metadata'sıdır (`DEC-032`). |
| `materials` | `serialized_assets` | 1 : 0..N | Asset yalnızca serialized material için. |
| `locations` | child `locations` | 1 : 0..N | `parent_id`. |
| `inventory_transactions` | `inventory_transaction_lines` | 1 : 1..N | Tamamlanmış başlık en az bir satır içerir. |
| `materials` | `inventory_transaction_lines` | 1 : 0..N | Her satır tam bir material'a bağlı. |
| `serialized_assets` | `inventory_transaction_lines` | 1 : 0..N | Quantity satırda null. |
| `locations` | source transaction lines | 1 : 0..N | `source_location_id`. |
| `locations` | target transaction lines | 1 : 0..N | `target_location_id`. |
| `material_conditions` | transaction lines | 1 : 0..N | Hareket türünden ayrı. |
| `inventory_transactions` | `issue_contexts` | 1 : 0..1 | Yalnızca ISSUE için zorunlu 1:1. |
| `employees` | `issue_contexts` | 1 : 0..N | Receiver UUID referansı; snapshot zorunlu (`DEC-024`). |
| `production_lines` | `issue_contexts` | 1 : 0..N | Hat UUID referansı; code/name snapshot zorunlu (`DEC-025`). |
| `inventory_transactions` | `correction_requests` | 1 : 0..N | `DEC-030`: aynı original transaction için eşzamanlı en fazla bir `PENDING` talep; terminal sonrası yeni talep açılabilir. |
| `correction_requests` | `correction_evidence` | 1 : 0..N | `DEC-034`: yeni talep için service `1..N` zorunlu; historical satırlar `0..N`. |
| `correction_requests` | result transaction | 1 : 0..1 | `DEC-030` downstream-owned one-to-one association; APPROVED ise zorunlu. Ledger reverse FK taşımaz. |
| `physical_count_sessions` | quantity lines | 1 : 0..N | Oturum en az bir quantity veya asset satırına sahip olmalı. |
| `physical_count_sessions` | asset lines | 1 : 0..N | Uzmanlaşmış serialized sayım satırı. |
| `import_batches` | `import_rows` | 1 : 1..N | Batch satırları staging'dir. |
| `materials` | `stock_balances` | 1 : 0..N | Yalnızca QUANTITY material. |
| `locations` | `stock_balances` | 1 : 0..N | Yalnız `active && can_hold_stock` lokasyon. |
| `material_conditions` | `stock_balances` | 1 : 0..N | Kondisyon partition'ı. |
| `materials/assets/locations` | first-party identity payload | 1 : 1 derived | V1'de tablo yok; UUID payload `DEC-036` ile türetilir. |
| `import_batches` | `inventory_baselines` | 1 : 0..N TBD | Tekrar/cutover politikası açık. |
| `physical_count_sessions` | `inventory_baseline_count_session_links` | 1 : 0..N | Yalnız `baseline_candidate=true` session linklenebilir; authoritative-establishment uniqueness guard ayrıca uygulanır. |
| `inventory_baselines` | `inventory_baseline_count_session_links` | 1 : 1..N | Bir baseline `DEC-033` gereği bir veya daha çok required count session kapsar. |
| `inventory_baselines` | `inventory_baseline_transaction_links` | 1 : 1..N | Establishment için bütün required scope'lar bağlı olmalı. |
| `inventory_transactions` | `inventory_baseline_transaction_links` | 1 : 0..1 | Link yalnız `INITIAL_BALANCE` transaction kabul eder. |

## 19. Constraints

### Constraint sınıflandırması

| Rule | DB | Service | Reason |
|---|:---:|:---:|---|
| `stock_balances.quantity >= 0` | Evet | Evet | DB son savunma, servis iş hatası mesajı ve atomik akış sağlar. |
| StockBalance composite uniqueness | Evet | Evet | DB duplicate partition'ı önler; servis doğru satırı kilitler. |
| Unique `operation_id` | Evet | Evet | DB yarışta duplicate'i keser; servis önceki sonucu döndürür. |
| Quantity/serialized line XOR | Evet | Evet | Satır check'i yapısal karışmayı önler; servis tracking mode'u doğrular. |
| Serialized line quantity NULL | Evet | Evet | Anonim quantity kullanımını önler. |
| Asset material = line material | Targeted DB guard | Evet | Catastrophic cross-table corruption; service + PostgreSQL constraint trigger/guard. |
| Material tracking mode compatibility | Targeted DB guard | Evet | SerializedAsset yalnız SERIALIZED; StockBalance yalnız QUANTITY. |
| Source != target when both present | Evet | Evet | Type'tan bağımsız row-local check güvenli ve ucuzdur. |
| Type-specific source/target requirements | Kısmen | Evet | Type başlıkta, lokasyon satırda; karmaşık cross-table check yerine servis. |
| Location `active && can_hold_stock` eligibility | Uygunsa targeted DB guard | Evet | Current master state cross-table kontrolüdür; historical referans sonradan bozulmamalı. |
| Negatif stok eşzamanlılık altında önlenir | Kısmen | Evet | DB transaction/row lock ve non-negative check birlikte gerekir. |
| Tamamlanmış transaction immutable | PostgreSQL trigger guard | Evet | ORM/Admin/raw application SQL UPDATE/DELETE yolunu DB keser. |
| ISSUE için tam bir IssueContext | Kısmen | Evet | Cross-table ve type-specific zorunluluk atomik serviste. |
| Correction approval role/state transition | Kısmen | Evet | State check DB'de; rol ve geçiş yetkisi serviste. |
| Serialized asset tek current location | Yapısal | Evet | Tek FK bir anda tek location tutar; ledger/state uyumu atomik serviste. |
| Count discrepancy doğrudan bakiye yazamaz | Yetki/policy | Evet | Yalnızca kontrollü inventory service bakiye değiştirebilir. |
| Import yetki bypass edemez | Yetki/policy | Evet | Staging'in ledger/balance'a doğrudan yazması yasaktır. |
| Barcode tam bir hedefe bağlı | Evet | Evet | Exactly-one check ve hedef aktiflik doğrulaması. |
| Correction kararı decider/time birlikteliği | Evet | Evet | Satır içi check uygundur; rol serviste. |
| Baseline yalnız reconciled count'tan kurulur | Targeted DB guard | Evet | Cross-table status/authority kontrolüdür. |
| Baseline link yalnız INITIAL_BALANCE type'a gider | Targeted DB guard | Evet | Link–transaction cross-table kuralı. |
| History sonrası tracking_mode değişmez | Targeted DB guard | Evet | Normal master update catastrophic history mismatch üretmemeli. |

### Normatif işlem yönü ve lokasyon doğrulama matrisi

Her transaction line için `source_location_id` o lokasyondaki stok/state'i **azaltır**, `target_location_id` o lokasyondaki stok/state'i **artırır**. Bu anlamın transaction-type-specific istisnası yoktur. Transaction type yalnız legal null/dolu kombinasyonunu belirler.

| Transaction type | Source | Target | Uygulama yeri |
|---|---|---|---|
| `RECEIPT` | Null | Zorunlu `active && can_hold_stock` target | Service + satır null check'leri |
| `ISSUE` | Zorunlu `active && can_hold_stock` source | Null; kullanım yeri `issue_contexts`te | Service |
| `RETURN` | Null | Zorunlu explicit target | `DEC-028` quantity first slice; original ISSUE line FK zorunlu, same material/unit/condition, cumulative cap. Target runtime `active && can_hold_stock` validation service fazındadır. |
| `TRANSFER` | Zorunlu stock-holding source | Zorunlu, farklı stock-holding target | Source!=target DB row check + service |
| `CONTROLLED_CORRECTION` | Azalış line'ında zorunlu | Artış line'ında zorunlu | `DEC-030`: one-line signed quantity veya two-line identity restatement; `corrected_line` zorunlu |
| `COUNT_RECONCILIATION` | Negatif farkta zorunlu | Pozitif farkta zorunlu | Yalnız tamamlanmış routine count discrepancy; tam bir QUANTITY line ve counting-owned immutable sonuç bağı |
| `INITIAL_BALANCE` | Null | Zorunlu stock-holding target | Yalnız baseline-owned scoped link ve inventory service |

Broader RETURN ve controlled correction semantics kesinleşmeden DB'ye ek zorunluluk gömülmez; yalnız `DEC-028` quantity first-slice guards current schema'ya aittir.

## 20. Index Strategy

Önerilen başlangıç indeksleri:

- `employees(employee_number)`
- `categories(parent_id)`, `categories(code)` karar sonrası unique
- `units_of_measure(code)` unique
- `material_conditions(code)` unique
- `materials(material_code)`, `materials(name)`, `materials(category_id)`, `materials(active)`
- `locations(parent_id)`, `locations(code)`, `locations(active)`
- `serialized_assets(internal_asset_code)` unique, `serialized_assets(serial_number)`, `serialized_assets(material_id)`, `serialized_assets(current_location_id)`
- `inventory_transactions(operation_id)` unique
- `inventory_transactions(occurred_at)`
- `inventory_transactions(transaction_type, occurred_at)`
- `inventory_transactions(acting_user_id, occurred_at)`
- `inventory_transaction_lines(transaction_id)`
- `inventory_transaction_lines(material_id)`
- `inventory_transaction_lines(serialized_asset_id)`
- `inventory_transaction_lines(source_location_id)` ve `(target_location_id)`
- `stock_balances(material_id, location_id, condition_id)` unique
- `correction_requests(status, requested_at)`, `(original_transaction_id)`
- `physical_count_sessions(status)`, `(reconciliation_status)`, `(scope_location_id)`
- `import_batches(status)`, `(source_checksum)`
- `import_rows(import_batch_id, validation_status)`
- first-party identity payload persist edilmez (`DEC-036`)
- `audit_events(occurred_at)`, `(entity_type, entity_id)`
- `inventory_baseline_transaction_links(inventory_baseline_id, scope_key)` unique ve transaction/operation unique indeksleri
- `inventory_baseline_count_session_links(inventory_baseline_id, physical_count_session_id)` unique

`technical_specs` için GIN, fuzzy name araması için trigram veya rapora özel indeksler gerçek sorgu ve veri hacmi ölçülmeden eklenmemelidir.

## 21. Delete / Retention Strategy

| Table | Strategy | Rationale / retention |
|---|---|---|
| `employees` | SOFT DELETE / DEACTIVATE | Hard delete surface yok; User link `SET_NULL`; issue snapshot'ları korunur (`DEC-024`). |
| `production_lines` | SOFT DELETE / DEACTIVATE | Hard delete surface yok; inactive yeni ISSUE seçiminde kullanılamaz (`DEC-025`). |
| `categories` | SOFT DELETE / DEACTIVATE | Material ve alt kategori bağları korunur. |
| `units_of_measure` | SOFT DELETE / DEACTIVATE | Tarihsel miktar anlamı korunur. |
| `material_conditions` | SOFT DELETE / DEACTIVATE | Ledger kondisyon geçmişi korunur. |
| `materials` | SOFT DELETE / DEACTIVATE | Ledger/asset geçmişi korunur. |
| `locations` | SOFT DELETE / DEACTIVATE | Eski transaction ve sayımlar anlaşılır kalır. |
| `serialized_assets` | NO HARD DELETE AFTER HISTORY | Fiziksel varlık kimliği ve geçmişi korunur. |
| `inventory_transactions` | IMMUTABLE / NO DELETE | Yetkili ledger. |
| `inventory_transaction_lines` | IMMUTABLE / NO DELETE | Yetkili ledger ayrıntısı. |
| `stock_balances` | SYSTEM-REBUILDABLE | Projection; kullanıcı düzenleme/silme yetkisi yok. |
| `issue_contexts` | IMMUTABLE / NO DELETE | Tarihsel alıcı ve kullanım snapshot'ı. |
| `correction_requests` | NO HARD DELETE AFTER SUBMISSION | Talep ve karar izi korunur. |
| `correction_evidence` | IMMUTABLE / NO HARD DELETE | Kanıt fotoğrafı metadata'sı; V1 otomatik silme yok (`DEC-034`). |
| `physical_count_sessions` | RETAIN / NO HARD DELETE | Mutabakat kanıtı. |
| `physical_count_quantity_lines` | RETAIN / NO HARD DELETE | Sayım snapshot ve fark izi. |
| `physical_count_asset_lines` | RETAIN / NO HARD DELETE | Tekil sayım izi. |
| `import_batches` | RETENTION TBD; COMMITTED RETAIN | Kaynak/commit denetimi. |
| `import_rows` | RETENTION TBD; COMMITTED RETAIN | Satır doğrulama izi. |
| first-party identity payload | NOT STORED | `DEC-036`; UUID already authoritative. |
| `audit_events` | IMMUTABLE; RETENTION TBD | İdari denetim izi. |
| `inventory_baselines` | IMMUTABLE / NO DELETE | Otorite cutover kanıtı. |
| `inventory_baseline_count_session_links` | IMMUTABLE / NO DELETE | Baseline count evidence lineage'i. |
| `inventory_baseline_transaction_links` | IMMUTABLE / NO DELETE | Scoped başlangıç ledger lineage'i. |

FK silme davranışı tarihsel tablolarda genel olarak `RESTRICT` olmalıdır. `CASCADE`, yalnızca henüz iş etkisi oluşturmamış geçici staging alt kayıtlarında retention politikası kesinleşince değerlendirilebilir.

## 22. Concurrency and Idempotency

Production transaction isolation assumption PostgreSQL default **`READ COMMITTED`**dır. Bütün inventory mutation'larda correctness sırası:

> **LOCK FIRST → CURRENT STATE'İ RE-READ → INVARIANT'LARI RE-VALIDATE → WRITE**

Lock öncesi validation yalnız erken kullanıcı feedback'idir; current-state correctness otoritesi değildir.

### Locking-sensitive alanlar

- **StockBalance değişikliği:** İlgili `(material, location, condition)` satırları aynı DB transaction içinde row-level lock ile okunup güncellenmelidir.
- **SerializedAsset state:** Asset satırı kilitlenmeli; ledger ve projection aynı transaction içinde güncellenmelidir.
- **Correction approval:** `PENDING` talep satırı kilitlenmeli; iki kararın eş zamanlı verilmesi engellenmelidir.
- **Import commit:** Batch kilitlenmeli; yalnızca doğrulanmış batch bir kez commit edilmelidir.
- **Physical reconciliation/baseline:** Count session ve baseline adayı kilitlenmeli; duplicate cutover engellenmelidir.

Phase 5.4C routine QUANTITY mutabakatında toplam kilit sırası `PhysicalCountSession → PhysicalCountQuantityLine → operation_id reservation → Material → Location → MaterialCondition → StockBalance`dır. Kilitli bakiye snapshot `expected_quantity` ile bire bir eşleşmezse typed drift conflict ile atomik rollback yapılır. Eksik bakiye current zero olarak karşılaştırılır; persisted zero satırı zorunlu değildir.

Phase 5.4D-A START, mevcut quantity snapshot kilitlerinden sonra in-scope `SerializedAsset` satırlarını kilitler ve expected serialized satırları oluşturur; candidate satırlar authoritative asset yazmaz.

Phase 5.4D-B establishment kilit sırası `InventoryBaseline → PhysicalCountSession → PhysicalCountQuantityLine → PhysicalCountSerializedLine → Material → Location → MaterialCondition → StockBalance → LOCK TABLE inventory_serializedasset IN EXCLUSIVE MODE → SerializedAsset`tır; child `INITIAL_BALANCE` kernel ardından operation-id reservation ve mevcut inventory lock sırasını izler. İki farklı baseline'ın eşzamanlı candidate promotion'ı EXCLUSIVE table lock ile serialize edilir.

Serialized ISSUE lock order: operation-id reservation → Material → source Location → MaterialCondition → Employee → ProductionLine → SerializedAsset.

Serialized unused RETURN lock order: operation-id reservation → original ISSUE line → Material → target Location → MaterialCondition → SerializedAsset.

Serialized TRANSFER lock order: operation-id reservation → Material → source ve target Location UUID-sıralı → MaterialCondition → SerializedAsset. Quantity TRANSFER ile aynı Location sıralaması korunur; Location ↔ SerializedAsset inversion yoktur.

Serialized RECEIVE lock yolu değişmez: operation-id reservation → Material → target Location → MaterialCondition → SerializedAsset insert. Asset satırı projection doğrulama/mutasyonundan önce kilitlenir.

Birden fazla quantity balance satırı `material_id → location_id → condition_id → primary key` sırasıyla kilitlenir. Serialized operation ilgili `SerializedAsset` satırını kilitler ve current location/condition/state'i lock sonrasında yeniden doğrular. Correction, import, reconciliation ve baseline kendi lifecycle/guard satırlarını lock altında yeniden doğrular.

Var olmayan `StockBalance` satırı `SELECT FOR UPDATE` ile kilitlenemez. Yeni `(material_id, location_id, condition_id)` anahtarı için kanonik kavramsal akış:

1. `BEGIN`;
2. composite `UNIQUE` koruması altında insert-with-conflict handling ile canonical satırın varlığını güvenceye al;
3. canonical satırı yeniden bul ve `SELECT ... FOR UPDATE` ile kilitle;
4. quantity/current state'i yeniden oku ve invariant'ları doğrula;
5. ledger'ı oluştur, projection'ı güncelle;
6. `COMMIT`.

Exact Django implementation savepoint + `IntegrityError`, PostgreSQL `INSERT ... ON CONFLICT` veya eşdeğer güvenli yöntem kullanabilir. Row locking tek başına nonexistent row yarışını çözmez. İki valid concurrent first receipt, aynı canonical balance row üzerinde birer kez etkili olmalıdır.

PostgreSQL/Django uygulama yönü:

- açık database transaction;
- uygun satırlarda `SELECT ... FOR UPDATE` / Django row-level locking;
- stok satırı yoksa yukarıdaki unique + conflict + re-lock stratejisi;
- DB check/unique kısıtlarının transaction sonunda son savunma olarak kullanılması;
- deadlock riskini azaltmak için kararlı lock order;
- beklenmeyen deadlock/transaction hatasında tam rollback; kısmi ledger/projection etkisi yok.

### Idempotency

Her envanter değiştiren komut `operation_id UUID` ve sunucunun semantic command payload'dan ürettiği `request_fingerprint` taşımalıdır.

- `inventory_transactions.operation_id` unique'dir.
- `request_fingerprint`, canonical server representation üzerinde SHA-256 hexadecimal digest'tir; client-supplied fingerprint güvenilmez.
- Fingerprint işlem türü, gerekli actor/context, material/asset, quantity, condition, source/target ve diğer semantic alanları içerir; operation ID, server timestamp ve presentation-only değerleri dışlar.
- Aynı `operation_id` + aynı fingerprint retry'ı ikinci etki yaratmadan önceki başarılı sonucu döndürür.
- Aynı `operation_id` + farklı fingerprint conflict üretir ve stok etkisi oluşturmaz.
- Concurrent same-ID submission'da DB uniqueness arbiter'dır; loser winning record'ı okuyup fingerprint'i karşılaştırır.
- Commit edilmiş transaction oluşmadan önceki failure retry için güvenlidir.
- Import master-data commit'i için `import_batches.operation_id` ve server fingerprint aynı korumayı sağlar; stock transaction üretmez.
- Bu yapı gelecekte mobil/zayıf bağlantı retry'ını destekler; offline sync protokolü tanımlamaz.

### Projection doğrulama ve repair

`StockBalance` ve `SerializedAsset` current state'in ledger ile uyumu teorik değil, işletilebilir olmalıdır:

- `verify_inventory_projection` benzeri read-only operation ledger'dan expected current state'i temporary/in-memory olarak kurar, persisted projection ile karşılaştırır ve fark raporlar;
- doğrulama varsayılan olarak veri değiştirmez;
- repair/rebuild ayrı privileged action'dır, önce dry-run gerekir, audit event üretir ve öncesinde backup önerilir;
- exact command name bağlayıcı değildir; capability inventory engine gate'inden ve kesinlikle pilot'tan önce zorunludur;
- automated testler ledger-derived ve persisted projection eşitliğini doğrular.

## 23. Historical Snapshot Strategy

### Zorunlu snapshot

`issue_contexts` aşağıdaki teslim anı değerlerini immutable saklar:

- `receiver_first_name_snapshot`
- `receiver_last_name_snapshot`
- `receiver_employee_number_snapshot`
- `production_line`
- `usage_location_text`

`receiver_employee_id` güncel kişi kaydına navigasyon sağlar; snapshot tarihsel anlamın kaynağıdır.

### Material ve Location

Material/lokasyon kod ve adlarını her transaction satırında tekrar etmek V1 için önerilmez:

- UUID FK ve hard delete yasağı referansın varlığını korur;
- master değişiklikleri `audit_events` ile izlenir;
- deactivation eski işlemi bozmaz;
- gereksiz kopyalar tutarsızlık ve depolama yükü oluşturur.

İş birimi eski raporların “o günkü görünen ad/kodla” bire bir basılması gerektiğini doğrularsa seçilmiş snapshot kolonları ayrıca eklenebilir. Bu gereksinim şu anda onaylı değildir.

## 24. Flexible Technical Attributes

`materials.technical_specs JSONB` önerilir:

- farklı elektrik malzemelerinin heterojen niteliklerini tek katalogda destekler;
- marka, model, kod, kategori, birim ve tracking mode gibi temel aranabilir alanlar relational kalır;
- kategori örnekleri görülmeden EAV tablo karmaşıklığı oluşturmaz.

`technical_specs`:

- stok miktarı,
- lokasyon,
- serialized current state,
- işlem geçmişi,
- yetki veya audit verisi

içeremez. JSON schema/doğrulama kuralları gerçek malzeme örnekleri ve Excel analizi sonrası belirlenir. GIN indeks yalnızca doğrulanmış arama path'leri için eklenir.

## 25. Minimum Stock Design

### Option A — `materials.minimum_stock_value`

Artıları:

- V1 için en basit yapı;
- mevcut “malzeme başına minimum stok” gereksinimini karşılar;
- Django ORM ve raporlama için nettir.

Eksileri:

- lokasyon/kondisyon bazlı birden fazla eşik desteklemez;
- aggregation semantics yine servis/reporting kararıdır.

### Option B — `minimum_stock_policies`

Olası alanlar material, location, condition scope, threshold ve active state olurdu. Ancak toplam/depo/lokasyon/kondisyon seçeneklerinden hangisinin iş kuralı olduğu bilinmediği için şu anda tablo tasarlamak varsayım ve gereksiz karmaşıklık üretir.

### Öneri

V1 başlangıcı için **Option A** önerilir. `minimum_stock_value` nullable ve non-negative tutulur; değerlendirme scope'u karar verilene kadar düşük stok hesaplaması tamamlanmış kabul edilmez.

İş kararı per-location veya çoklu policy gerektirirse:

1. ayrı `minimum_stock_policies` tablosu eklenir;
2. mevcut material değeri global/default policy'ye kontrollü taşınır;
3. geçiş sonrası material kolonu kaldırma kararı migration planında verilir.

Bu yol, açık iş semantiğini şimdi uydurmadan gelecekteki genişlemeyi mümkün kılar.

## 26. ER Diagram

```mermaid
erDiagram
    EMPLOYEE o|--o{ APPLICATION_USER_PROFILE : links
    CATEGORY ||--o{ CATEGORY : parent
    CATEGORY ||--o{ MATERIAL : classifies
    UNIT_OF_MEASURE o|--o{ MATERIAL : measures
    MATERIAL ||--o{ SERIALIZED_ASSET : defines
    LOCATION ||--o{ LOCATION : parent
    LOCATION o|--o{ SERIALIZED_ASSET : current_location
    MATERIAL_CONDITION o|--o{ SERIALIZED_ASSET : current_condition
    INVENTORY_TRANSACTION ||--|{ INVENTORY_TRANSACTION_LINE : contains
    MATERIAL ||--o{ INVENTORY_TRANSACTION_LINE : referenced_by
    SERIALIZED_ASSET o|--o{ INVENTORY_TRANSACTION_LINE : moved_by
    MATERIAL_CONDITION ||--o{ INVENTORY_TRANSACTION_LINE : conditions
    LOCATION o|--o{ INVENTORY_TRANSACTION_LINE : source
    LOCATION o|--o{ INVENTORY_TRANSACTION_LINE : target
    INVENTORY_TRANSACTION ||--o| ISSUE_CONTEXT : issue_details
    INVENTORY_TRANSACTION ||--o{ CORRECTION_REQUEST : corrected_by
    CORRECTION_REQUEST ||--o{ CORRECTION_EVIDENCE : evidenced_by
    PHYSICAL_COUNT_SESSION ||--o{ PHYSICAL_COUNT_QUANTITY_LINE : counts
    PHYSICAL_COUNT_SESSION ||--o{ PHYSICAL_COUNT_ASSET_LINE : counts
    IMPORT_BATCH ||--|{ IMPORT_ROW : stages
    MATERIAL ||--o{ STOCK_BALANCE : projected_as
    LOCATION ||--o{ STOCK_BALANCE : located_at
    MATERIAL_CONDITION ||--o{ STOCK_BALANCE : partitioned_by
    MATERIAL o|--o{ BARCODE_IDENTIFIER : identified_by
    SERIALIZED_ASSET o|--o{ BARCODE_IDENTIFIER : identified_by
    LOCATION o|--o{ BARCODE_IDENTIFIER : identified_by
    PHYSICAL_COUNT_SESSION ||--o{ INVENTORY_BASELINE : reconciled_from
    IMPORT_BATCH o|--o{ INVENTORY_BASELINE : candidate_for
    INVENTORY_BASELINE ||--|{ INVENTORY_BASELINE_TRANSACTION_LINK : scopes
    INVENTORY_TRANSACTION ||--o| INVENTORY_BASELINE_TRANSACTION_LINK : initial_balance
```

`BARCODE_IDENTIFIER` için üç çizgi alternatif hedefleri gösterir; exactly-one-target kuralı check constraint ile uygulanır. `AUDIT_EVENT` generic typed reference ile çalışır ve inventory ledger ilişkisini kopyalamaz.

## 27. Open Decisions

### BLOCKS IMPLEMENTATION

Bu legacy tablo güncel karar statüsünün kanonik kaydı değildir. `docs/06-DECISION-REGISTER.md` resolved kararları, open kararları ve hard gate'leri eşler.

| ID | Decision | Etkilenen model |
|---|---|---|
| DM-B01 | `employee_number` global unique, editable, eski numara ayrı registry ile rezerve edilmez (`DEC-024`); `material_code` remainder açık | Unique constraints, import duplicate yönetimi |
| DM-B02 | `DEC-032`: `internal_asset_code` zorunlu/global unique; `serial_number` optional/material içinde unique; UUID technical identity | `serialized_assets` |
| DM-B03 | `DEC-032`: Phase 5.3 yalnız `IN_STOCK`; broader lifecycle vocabulary deferred | Serialized projection |
| DM-B04 | Bozuk/çıkma kondisyon kullanılabilir ve minimum stoğa dahil mi? | `stock_balances`, reporting, service |
| DM-B05 | Minimum stok toplam, depo, lokasyon veya kondisyon bazında mı? | Material column veya policy table |
| DM-B06 | Ondalık hassasiyet, kısmi miktar ve unit conversion davranışı nedir? | `NUMERIC` doğrulaması, UoM |
| DM-B07 | ProductionLine `DEC-025` ile kararlı dynamic entity; exact usage place ayrı free text; quantity ISSUE first slice `DEC-027` doğrultusunda Phase 4.2A–4.2C COMPLETE | `production_lines`, `issue_contexts` |
| DM-B08 | `ApplicationUser`–`Employee` nullable one-to-one ownership Employee (`DEC-024`); rol atama/onay `DEC-022` ile kararlı | Identity FK/unique ve permissions |
| DM-B09 | Location type/hiyerarşi/kod ve stoklu deactivation `DEC-023` ile kararlıdır. Inventory aynı lifecycle invariant'ını otoritatif uygulamak zorundadır. | `locations` |
| DM-B10 | Olağan değişiklik `DEC-013` ile yasak; exceptional migration istenirse ayrı iş kararı gerekir. | `materials`, ledger validation |
| DM-B11 | `DEC-028` unused linked QUANTITY RETURN için kararlı; broader serialized/used/defective/unknown-provenance RETURN açık kalır | Ledger service/FK kuralları |
| DM-B12 | Transfer yetkileri ve zorunlu iş senaryoları nelerdir? | Permissions ve line validation |
| DM-B13 | `DEC-030`: quantity correction bounds/lineage ve requester≠decider; broader correction deferred | `correction_requests`, downstream-owned transaction association |
| DM-B14 | **DECIDED** (`DEC-033`): no-freeze immutable snapshot/drift refusal, zero tolerance, approver ve completion ölçütleri | Count session, baseline |
| DM-B15 | Bir baseline bir mi birden çok count session'a mı bağlanır? | `inventory_baselines` cardinality |
| DM-B16 | `DEC-006`–`DEC-009` ile Gate 0'da kapatıldı: unique + conflict + lock, READ COMMITTED, lock order, fingerprint | StockBalance concurrency |

### CAN IMPLEMENT WITH SAFE DEFAULT

| ID | Safe technical default | Açık kalan iş kararı |
|---|---|---|
| DM-S01 | UUID PK ve `TIMESTAMPTZ` kullan. | İş anlamını değiştirmez. |
| DM-S02 | `NUMERIC(18,3)` kullan; unit'e göre daha dar validation sonradan ekle. | Kesin decimal policy DM-B06. |
| DM-S03 | Master kayıtlarda deactivation, ledger'da no-delete kullan. | Retention süreleri ayrı karar. |
| DM-S04 | `technical_specs JSONB` kullan, GIN ekleme. | Teknik nitelik şeması Excel sonrası. |
| DM-S05 | Serialized current state'i ledger ile atomik projection olarak persist et. | İlk vocabulary `DEC-032` ile yalnız `IN_STOCK`. |
| DM-S06 | Generic content-type yerine barcode için üç nullable FK + exactly-one check kullan. | Payload/type sözlüğü açık. |
| DM-S07 | Rejection reason nullable kalsın. | Zorunluluk hâlâ PROPOSED. |
| DM-S08 | Material/location ad-kod snapshot'ı alma; FK + deactivation + audit kullan. | Eski görünen değerle rapor ihtiyacı doğrulanırsa eklenir. |
| DM-S09 | Import row verisini JSONB staging'de tut; validated commit dışında domain yazımı yapma. | Excel mapping açık. |
| DM-S10 | Transaction ledger'a yalnız completed event yaz; mutable draft status tutma. | UI taslak ihtiyacı ayrıca değerlendirilebilir. |

### CAN WAIT UNTIL PILOT

| ID | Decision | Reason |
|---|---|---|
| DM-P01 | Inventory, audit, attachment ve import retention süreleri | Şema no-delete güvenli yönle başlayabilir. |
| DM-P02 | QR payload, semboloji, yazıcı ve etiket ayrıntıları | Generic identifier modeli cihazdan bağımsızdır. |
| DM-P03 | Rapor indeksleri, JSONB GIN ve name-search optimizasyonu | Gerçek sorgu/veri hacmiyle ölçülmelidir. |
| DM-P04 | Nicel stok doğruluk hedefi ve kabul sapması | Pilot ölçümüyle kalibre edilebilir. |
| DM-P05 | Minimum stok uyarı kanalı | Projection, bildirim kanalından bağımsızdır. |
| DM-P06 | Fotoğraf ve import dosyası storage teknolojisi | Storage key soyutlaması seçimi erteler. |

### Kaynak belgelerle uyum

Gate 0 remediation ile `INITIAL_BALANCE` Domain Model'e baseline-only teknik ledger türü olarak eklenmiş, candidate inventory staging-only olarak netleştirilmiş ve reverse workflow FK'leri kaldırılmıştır. Açık iş kararlarının kanonik statüsü `docs/06-DECISION-REGISTER.md`de tutulur.

## 28. Gate 0 Sonrası Uygulama İçin Girdi

Sonraki uygulama ve schema implementation review aşağıdaki tasarım kararlarını korumalıdır:

1. Önerilen 23 uygulama tablosu ve Django auth ile link sınırı.
2. UUID PK, timezone-aware timestamps, `NUMERIC(18,3)` quantity ve kontrollü JSONB kullanımı.
3. `materials` üzerinde yetkili mutable stok alanı bulunmaması.
4. Immutable `inventory_transactions` / `inventory_transaction_lines` ledger modeli.
5. Quantity/serialized XOR satır kısıtı ve serialized quantity'nin null olması.
6. `stock_balances` için `(material_id, location_id, condition_id)` unique key ve non-negative check.
7. Serialized current location/condition'ın ledger-authoritative, atomik persisted projection olması.
8. `issue_contexts`te zorunlu alıcı snapshot alanları.
9. Düzeltmenin özgün transaction'ı koruması ve sonucu yeni kontrollü ledger etkisine bağlaması.
10. Quantity ve serialized fiziksel sayım satırlarının ayrı tutulması.
11. Import commit'in sıfır stock ledger/`StockBalance` etkisi; candidate data'nın staging-only olması.
12. `INITIAL_BALANCE`ın yalnız `InventoryBaseline` üzerinden `1..N` scoped kontrollü ledger olayı olması.
13. First-party identity payload persist edilmez; Code128/QR aynı compact `TZ1M|A|L:<22-char-base64url-uuid>` metnini taşır (`DEC-036`).
14. Ledger, balance, correction, import ve baseline için transaction/locking/idempotency gereksinimleri.
15. `docs/06-DECISION-REGISTER.md` içindeki hard gate'lerin varsayımla kapatılmaması.

Phase 1 otomatik başlamaz. Gate 0 remediation bağımsız re-audit ile doğrulanmadan yürütülebilir model/migration üretilmemelidir.
