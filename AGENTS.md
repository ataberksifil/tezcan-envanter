# Repository Rules — Electrical Workshop Inventory System

Bu dosya repository genelinde her zaman geçerlidir. AI coding agent'ları ve insan katkıcılar göreve başlamadan önce bu dosyayı ve ilgili `docs/` belgelerini okumalıdır. Buradaki kurallar açıkça güncellenmedikçe zorunludur.

## 1. Kaynak Otoritesi ve Kapsam

Yetkili belgeler:

1. `docs/00-PRODUCT.md`
2. `docs/01-BUSINESS-RULES.md`
3. `docs/02-DOMAIN-MODEL.md`
4. `docs/03-DATA-MODEL.md`
5. `docs/04-USER-FLOWS.md`
6. `docs/05-ARCHITECTURE.md`
7. `docs/06-DECISION-REGISTER.md` — karar status, hard gate ve Gate 0 audit disposition otoritesi
8. `AGENTS.md`

Alt seviye teknik karar, üst seviye iş gereksinimini sessizce değiştiremez. Çelişki görülürse uygulamayı durdur, çelişkiyi dosya ve kural kimliğiyle raporla. İstenen görev dışındaki dosyaları veya roadmap adımlarını otomatik başlatma.

## 2. Canonical Phase 0 Roadmap

Tek geçerli sıra:

1. **0.1 Product Requirements**
2. **0.2 Business Rules**
3. **0.3 Domain Model**
4. **0.4 Data Model**
5. **0.5 User Flows**
6. **0.6 Technical Architecture**
7. **0.7 AGENTS.md / repository rules**
8. **0.8 Architecture Audit**
9. **0.9 Gate 0 Remediation**
10. **Gate 0 independent re-audit**

Re-audit başarılı olmadan Phase 1 otomatik başlamaz. Eski belgelerdeki farklı Task açıklamaları bu sırayı geçersiz kılamaz.

## 3. Ürün Önceliği

Temel ürün vaadi:

> Sistem bir malzemenin tanımlı depolama konumunda bulunduğunu söylüyorsa malzeme fiziksel olarak orada bulunabilmelidir.

Karar önceliği:

> data integrity > inventory correctness > auditability > operational simplicity > visual sophistication

Görsel kolaylık için zorunlu veri, kontrol, iz veya yetki kaldırma.

## 4. Zorunlu Mimari ve Stack

- Mimari **modüler monolit**tir.
- V1 stack: Python, Django 5.2 LTS, Django Templates, HTMX, Bootstrap 5, PostgreSQL, Django ORM/migrations, Django Auth/Groups/Permissions, openpyxl, pytest/Django tests, Django storage abstraction arkasında filesystem storage ve Git.
- `AUTH_USER_MODEL`, ilk Django migration'dan itibaren minimal proje sahipli `accounts.User` (`AbstractUser`) olmalıdır (`DEC-019`). Django default `User`'a geri dönme. `Employee`'yi `User`'a birleştirme; employee business alanları `User`'a taşınmaz (`DEC-024`).
- Business transaction zamanları timezone-aware saklanır; kullanıcı sunumunda varsayılan `Europe/Istanbul`dır.
- PostgreSQL production kullanımı ve deployment şekli IT onayına bağlıdır; SQLite production fallback oluşturma.
- Mimari kararı sessizce değiştirme.

## 5. Yasak Mimari Genişleme ve Dependencies

Açık mimari onay olmadan ekleme:

- React, Next.js veya ayrı SPA
- microservices veya Kubernetes
- Redis veya Celery
- event sourcing veya CQRS
- core inventory için dış cloud bağımlılığı
- ayrı/genel REST API
- Elasticsearch, data warehouse veya gereksiz cache altyapısı

Yeni üçüncü taraf package öncesinde:

1. Standard Python/Django'nun neden yeterli olmadığını açıkla.
2. Bakım, güvenlik, lisans ve deployment etkisini belirt.
3. Gerekçeyi review için kaydet.

Ölçülmüş ihtiyaç yoksa dependency ekleme.

## 6. Module Ownership

Yalnız şu ana modülleri kullan:

| Modül | Sahiplik | Yasak |
|---|---|---|
| `accounts` | Employee, user/auth entegrasyonu | Stock mutation |
| `catalog` | Category, UnitOfMeasure, MaterialCondition, Material | Stock/ledger mutation |
| `locations` | Location hiyerarşisi | Inventory mutation |
| `inventory` | InventoryTransaction/Line, StockBalance, SerializedAsset inventory state, IssueContext ve tüm stok servisleri | Correction/import/report workflow sahipliği |
| `corrections` | CorrectionRequest ve kanıt fotoğrafı workflow'u | StockBalance/ledger'ı doğrudan yazmak |
| `counting` | Physical count ve reconciliation workflow'u | StockBalance'ı doğrudan yazmak |
| `imports` | Excel staging, validation, preview | Doğrudan authoritative stock oluşturmak |
| `reports` | Salt okunur sorgular ve Excel export | Business data mutation |
| `audit` | Non-stock administrative audit | Ledger'ı gereksiz kopyalamak |
| `core` | Teknik ortak yardımcılar/storage/error/clock | Domain logic dumping ground olmak |
| `identification` (QR feature başladığında) | BarcodeIdentifier registry ve authenticated resolver | Stock mutation veya core modüllerin kendisine bağımlı olması |

`attachments` V1'de ayrı app olmak zorunda değildir; correction modeli `corrections`, storage adaptörü `core` tarafından sahiplenilebilir.

## 7. Dependency Safety ve Identification

- Circular Python dependency oluşturma.
- `catalog` ve `locations`, mutation yapmak için `inventory` import edemez.
- `corrections`, `counting` ve `imports` stok etkisi için public inventory service/use-case çağırır.
- `reports` yalnız okur.
- `accounts` ile `audit` arasında çift yönlü import oluşturma. Audit actor ilişkisini stable ID/lazy FK ve dar append API ile gevşek bağlı tut.
- `audit`, business modüllerini import edip workflow çalıştırmaz.
- `core`, hiçbir business modülüne bağımlı olmaz.

`BarcodeIdentifier`ı `catalog` içine kalıcı yerleştirme. QR feature başladığında neutral `identification` modülü oluşturulabilir; ihtiyaçtan önce oluşturma. `identification`, `catalog`, `inventory` ve `locations`a bağımlı olabilir; bu modüller core domain operasyonu için `identification`a bağımlı olmaz.

`InventoryTransaction`, downstream `corrections`, `counting` veya `imports` modellerine reverse FK taşıyamaz. Correction/baseline gibi workflow sonuç bağlantısının sahibi downstream module'dür; aynı ilişki iki yönde redundant FK ile tutulmaz. `source_transaction_id` inventory içinde self-reference olarak kalabilir.

## 8. Inventory Golden Rule ve Service Layer

Stock'u hiçbir zaman doğrudan değiştirme:

- view
- form
- template
- Django Admin
- import code
- count code
- correction workflow code

Tüm inventory mutation'lar explicit inventory application service/use-case üzerinden geçer:

- `receive_stock`
- `issue_stock`
- `return_stock`
- `transfer_stock`
- `apply_controlled_correction`
- `reconcile_physical_count`
- `establish_initial_balance`

İsimler kavramsaldır; imzaları ilgili implementation görevi belirler. Form alan/format doğrular; service yetki, business invariant, transaction, lock, ledger ve projection bütünlüğünü yönetir.

Her `InventoryTransactionLine` için kanonik yön anlamı istisnasızdır:

- `source_location` o lokasyondaki stock/state'i azaltır.
- `target_location` o lokasyondaki stock/state'i artırır.
- İki alan da doluysa farklı olmalıdır; DB row check ve service validation birlikte korur.
- Transaction type bu anlamı değiştirmez; yalnız legal source/target kombinasyonunu belirler.

## 9. Ledger ve StockBalance

`InventoryTransaction + InventoryTransactionLine` authoritative inventory ledger'dır.

- Completed transaction immutable'dır.
- Committed `InventoryTransaction` ve `InventoryTransactionLine` ordinary `UPDATE`/`DELETE`e karşı PostgreSQL DB-level immutability guard ile korunur. Preferred implementation migration-managed trigger'dır.
- Silent edit ve normal hard delete yasaktır.
- Correction yeni, bağlantılı inventory etkisi oluşturur; original transaction görünür kalır.
- `Material` üzerinde authoritative `stock_quantity` bulunamaz.
- Bu ledger full event sourcing değildir.

DB guard; Admin, `queryset.update`, `bulk_update` ve raw application SQL yolunu da kesmelidir. Exceptional repair/migration bypass yalnız explicit, privileged, documented ve audited olabilir; normal uygulamada kalıcı disable flag yoktur.

`StockBalance` current-state projection'dır:

- Yalnız `QUANTITY` material kullanır.
- Kimlik yönü `(material, location, condition)`dır.
- Quantity negatif olamaz.
- Kullanıcı/admin tarafından elle düzenlenemez.
- Yalnız inventory service, ledger ile aynı transaction içinde günceller.
- İlgili satırlar concurrency altında korunur.
- Ledger'dan doğrulanabilir/yeniden üretilebilir olmalıdır.

`StockBalance` yalnız `QUANTITY` material ve uygun stock-holding location için var olabilir. `SerializedAsset.material` `SERIALIZED` olmalı, serialized line material'ı asset material'ıyla aynı olmalıdır. Bu catastrophic cross-table invariant'lar service validation ve targeted PostgreSQL DB integrity guard/constraint trigger ile korunur; sırf composite FK simülasyonu için redundant tracking-mode kolonu varsayılan çözüm değildir.

Projection doğrulaması teorik olamaz. Pilot öncesinde ledger'dan expected quantity/serialized current state'i temporary olarak üretip persisted projection ile karşılaştıran, default read-only bir `verify_inventory_projection` capability'si bulunmalıdır. Repair/rebuild ayrı privileged, audited, dry-run sonrası ve mümkünse backup sonrası kullanılan operasyondur; otomatik çalışmaz.

## 10. Serialized Assets, Tracking Mode ve Condition

- `SerializedAsset` tam bir fiziksel item'ı temsil eder.
- Asset'ın `Material.tracking_mode` değeri `SERIALIZED` olmalıdır.
- Tek asset aynı anda iki current physical location'da bulunamaz.
- Current state persisted projection/cache olabilir; ledger authoritative kalır.
- Movement sırasında asset row concurrency-safe kilitlenir.
- Quantity ve serialized transaction şekilleri karıştırılamaz.

Geçerli tracking mode'lar:

- `QUANTITY`
- `SERIALIZED`

Category'den kalıcı tracking mode türetme. Material herhangi bir inventory ledger history taşıdıktan sonra tracking mode normal uygulama yollarında değiştirilemez. Exceptional dönüşüm istenirse ayrı iş kararı, migration planı ve review gerekir.

Movement type ile condition ayrıdır. Başlangıç condition kodları:

- `NEW_GOOD`
- `USED_REMOVED_GOOD`
- `DEFECTIVE`
- `USED_REMOVED_DEFECTIVE`

Defective/used durumlarını keyfî transaction type'a dönüştürme. Condition'ın available/minimum stock etkisi **TBD**'dir.

## 11. Transaction, Concurrency ve Negative Stock

Her inventory mutation:

1. Actor'ı authenticate/authorize eder.
2. Active master data ve tracking mode'u ön doğrular.
3. DB transaction başlatır.
4. `operation_id` kontrol eder.
5. Gerekli current-state satırlarını kilitler.
6. Yetki/master/business invariant'ları transaction içinde tekrar doğrular.
7. Ledger kaydını oluşturur.
8. Projection'ı günceller.
9. IssueContext/correction/count/audit gibi ilişkili kayıtları oluşturur.
10. Atomik commit eder.

Herhangi bir hata: **ROLLBACK**. Kısmi stok etkisi yasaktır.

PostgreSQL transaction ve row-level locking kullan. Birden fazla satır kilidinde mümkünse deterministic ordering uygula.

Production isolation assumption PostgreSQL default `READ COMMITTED`dır. Current-state correctness sırası:

> lock first → current state'i re-read → invariant'ları re-validate → write

Lock öncesi stok validation'ına güvenme. Quantity balance lock order: `material_id → location_id → condition_id → primary key`.

`SELECT FOR UPDATE` var olmayan `StockBalance` satırını kilitlemez. İlk `(material, location, condition)` kaydı için composite `UNIQUE` + safe insert/on-conflict + canonical row re-read/lock + mutation contract zorunludur. Savepoint/`IntegrityError`, PostgreSQL `ON CONFLICT` veya eşdeğer güvenli uygulama seçilebilir; row locking tek başına yeterli değildir.

Zorunlu race testleri:

- concurrent last-quantity issue
- concurrent serialized movement
- correction double approval
- reconciliation double commit
- import double commit
- baseline double establishment

Beklenmeyen deadlock/transaction hatası tam rollback üretmelidir; kısmi ledger/projection etkisi yasaktır.

Negatif stok concurrent istekler dahil yasaktır. UI kontrolü yeterli değildir; service + transaction/locking + uygulanabilir DB constraint birlikte korur.

## 12. Idempotency

Her inventory-changing command `operation_id UUID` ve server-generated `request_fingerprint` taşır.

- DB'de unique olmalıdır.
- Aynı command/payload retry ikinci inventory etkisi yaratmaz.
- Başarılı önceki sonuç mümkünse geri döndürülür.
- Fingerprint, canonical semantic command payload üzerinde SHA-256 hexadecimal digest'tir; client hash'ine güvenilmez.
- Operation type, gerekli actor/context, material/asset, quantity, condition, source/target ve semantic alanları kapsar; operation ID, server timestamp ve presentation-only data'yı dışlar.
- Aynı ID + aynı fingerprint önceki başarılı sonucu döndürür.
- Aynı ID + farklı fingerprint conflict'tir ve inventory etkisi oluşturmaz.
- Concurrent same-ID submission'da DB uniqueness arbiter'dır; loser winning row'u okuyup fingerprint'i karşılaştırır.
- Commit edilmiş transaction oluşmadan önceki failure retry edilebilir.
- Double-click, network retry ve gelecekteki mobile/offline için bu korumayı kaldırma.
- Offline sync protokolünü açık görev olmadan tasarlama.

## 13. Permissions ve ISSUE Verisi

Server-side authorization zorunludur; gizli/disabled button güvenlik değildir.

| Rol | Onaylı yetkiler |
|---|---|
| `TECHNICIAN` | View inventory, issue, correction request; normal receipt ve correction approval yok |
| `STOREKEEPER` | View, receipt, issue; diğer operational permissions TBD |
| `ADMIN_MANAGER` | Full application management, master data, correction approval ve controlled correction |

Phase 3 managed Location permissions (`DEC-023`, `DEC-022` item 13): `locations.view_location`, `locations.add_location`, `locations.change_location`. Application access management `delete_location` expose etmez. `add_location` veya `change_location`, `view_location` gerektirir. Varsayılan şablonlar ileride `TECHNICIAN`/`STOREKEEPER` için `view_location`, `ADMIN_MANAGER` için view/add/change içerebilir. `setup_roles` non-destructive kalır; mevcut Group'lara sessizce yeni izin verilmez.

Return, transfer, count/reconciliation ve reporting permission'larını uydurma.

`DEC-HG-005`: Prior ISSUE zorunluluğu, partial quantity, returned condition'ı belirleyen aktör, serialized current-state ve sistemde issue edilmemiş wrong-delivery/found material davranışı çözülmeden RETURN schema/service/UI veya aktif menü oluşturma.

Her `ISSUE` şunları immutable tarihsel bağlam olarak korur:

- receiver first name
- receiver last name
- receiver employee number
- production line
- actual usage location
- system-recorded transaction time
- acting application user

Employee master değişse bile receiver snapshot okunabilir kalır. Normal kullanıcı original transaction timestamp'i değiştiremez.

`DEC-HG-003` (**DECIDED**, `DEC-025`): `ProductionLine` dynamic master-data entity (`inventory` app); recursive hierarchy; code/name lifecycle. Exact usage place ayrı required free text; `UsagePlace` modeli yok. ISSUE data/UI inventory hard gate'lerini bekler; fabrika hatları uydurulmaz/seed edilmez.

`DEC-HG-004` (**DECIDED**, `DEC-024`): `accounts.Employee` ayrı entity; sicil string/global unique/editable; nullable one-to-one User link (ownership Employee, SET_NULL). Phase 3.2 Employee foundation implementation sıradaki adım. Personal/photo/audit retention pilot öncesi karara bağlanır; receiver snapshot her durumda korunur. Managed permissions: `accounts.view_employee`, `accounts.add_employee`, `accounts.change_employee`.

## 14. Corrections

`CorrectionRequest` için zorunlu:

- original transaction
- requester
- explanation
- current supporting photo
- request timestamp

`PENDING` talep stock değiştirmez. Approval:

- yalnız `ADMIN_MANAGER`
- concurrency-safe
- original transaction'ı korur
- controlled, traceable inventory effect üretir
- decision actor/time'ı korur

Rejection reason yalnız **PROPOSED**'dır; onay gelmeden mandatory constraint/form kuralı yapma.

`DEC-HG-002`: Tek/cumulative approved correction, partial correction, original-line reference, over-correction, correction after later movement, correction-of-correction lineage, requester=approver ve current stock'un negative correction'ı karşılayamaması karara bağlanmadan correction schema/service implementation başlatma.

## 15. Physical Count, Import ve Baseline

Physical count doğrudan `StockBalance` overwrite edemez:

> count → discrepancy → review → authorized reconciliation → ledger → projection

Quantity count numeric amount; serialized count asset identity/presence kullanır.

Excel import sırası:

> Excel → ImportBatch/ImportRow → parse → validation → preview → controlled master-data commit → staging-only candidate/count reference → physical count → reconciliation → baseline → scoped INITIAL_BALANCE → authoritative stock

- Blind/direct DB import yasaktır.
- Upload/commit `StockBalance` veya stock-changing `InventoryTransaction` yazamaz.
- **CANDIDATE INVENTORY IS NOT LEDGER DATA AND IS NOT STOCK.**
- Imported quantity yalnız `ImportRow.mapped_data` veya eşdeğer staging metadata'da comparison/count preparation için kalabilir.
- Import commit kontrollü Material/Category/Location master data ve outcome metadata oluşturabilir.
- Gerçek workbook görülmeden mapping uydurma.

Sistem ancak controlled physical reconciliation ve baseline/cutover sonrası authoritative olur. Açılış stoğu tam bir kez, yalnız baseline-owned `INITIAL_BALANCE` transaction'larla oluşur; arbitrary adjustment shortcut olarak kullanılamaz.

Bir `InventoryBaseline`, downstream-owned association ile `1..N` scoped `INITIAL_BALANCE` transaction'a bağlanabilir. Her scope idempotent olmalı; baseline tüm required scope'lar commit edilip projection verify başarılı olduktan sonra `ESTABLISHED` olmalı; aynı cutover context için en fazla bir authoritative established baseline bulunmalıdır. Inventory ledger baseline'a reverse FK taşımaz.

`DEC-HG-001`: Stock-stability strategy seçilmeden physical count/reconciliation schema, service veya UI başlatma. Scoped freeze, as-of snapshot + movement replay veya güvenliği kanıtlanmış revalidation/reconfirmation seçeneklerinden biri açıkça seçilmelidir. Seçimden bağımsız explicit scope/expected timing, lock altında revalidation, idempotent reconciliation, double-apply guard ve serialized missing/unexpected resolution zorunludur.

## 16. Locations, Quantities ve Database

- Locations hierarchical'dır; shelf-level inventory zorunludur.
- Hiyerarşi dinamik recursive parent ile arbitrary depth'tir; sabit warehouse/shelf/line enum ve generic tree framework yoktur (`DEC-023`).
- UUID kalıcı kimliktir; `code` globally unique, case-sensitive, editable; regex/forced case yoktur.
- `name` required, trimmed, non-unique, editable.
- Path/depth cache bu fazda persist edilmez.
- `Location.can_hold_stock` explicit capability'dir; leaf/child/name/hierarchy depth veya type'tan türetilmez. Default `False`. Parent ve child bağımsız olarak stok tutabilir.
- Fiziksel stok yalnız `active=true AND can_hold_stock=true` location'a yeni olarak yerleştirilebilir.
- Inactive location yeni operational inventory alamaz; historical location reference okunabilir kalır.
- Inactive parent'ın active child'ı olabilir; parent status children'a cascade etmez.
- Circular parent hierarchy yasaktır; self-parent ve descendant/cycle parenting yasaktır; parent sonradan değiştirilebilir.
- Referans verilen location hard delete edilmez; hard delete iş operasyonu yoktur.
- Yetkili envanter varken non-zero stock'lu Location pasifleştirilemez ve `can_hold_stock` True→False yapılamaz; stok önce taşınmalı/mutabakatla sıfırlanmalıdır (`DEC-023`). Inventory entegrasyonu aynı invariant'ı otoritatif uygular. Phase 3.1 CRUD, stok satırı yokken güvenle uygulanabilir.
- Zorunlu `location_type` Phase 3.1'de yoktur.
- Location writable Django Admin yüzeyi yoktur; Yönetim shell + service + `AuditEvent` kullanılır.
- Tahmini fabrika Location satırları seed edilmez.

Inventory quantity için float kullanma. Current direction `NUMERIC(18,3)`tır. Her unit'in decimal kabul ettiğini varsayma; per-unit precision **TBD**'dir.

PostgreSQL semantics kullan. Schema değişikliği Django migration gerektirir ve review edilmelidir. Açık onay olmadan destructive/manual production schema değişikliği, migration reset veya constraint disable yapma. Structural invariant'larda DB constraint tercih et; cross-table business rule'ları service layer'da da uygula.

## 17. Django Admin

Django Admin'de arbitrary edit sunma:

- `StockBalance`
- completed `InventoryTransaction`
- `InventoryTransactionLine`
- `SerializedAsset` current inventory state
- workflow dışı correction approval alanları
- established baseline

Protected model Admin'lerinde ordinary add/change/delete permission kapalı, readonly alanlar explicit, transaction-line inline ve destructive bulk action (`delete_selected` dahil) disabled olmalıdır.

Location writable Django Admin yüzeyi yoktur; Location mevcut Yönetim shell, service-layer mutation ve `AuditEvent` ile yönetilir (`DEC-023`).

Master data kontrollü admin ekranlarıyla yönetilebilir. Admin action/save stok değiştiriyorsa aynı inventory service yolunu kullanmalıdır; doğrudan ORM mutation yasaktır. Admin lockdown DB guard'ın yerine geçmez. Shell/dbshell/`loaddata`/management command Admin koruması dışında olsa da ledger immutability ve cross-table DB guards'ı aşamaz.

## 18. Files ve QR

Correction photo:

- metadata DB'de
- binary Django storage abstraction ile
- type/content/size validation
- randomized internal storage key
- user-controlled filesystem path yok
- executable upload yok
- sensitive evidence için public `MEDIA_URL` değil authenticated, object-level permission-checked application path
- media backup kapsamına dahil

Büyük image binary'sini PostgreSQL'e koyma. Retention **TBD**'dir.

File/DB commit sırası: validate/upload → persistent storage'da finalize → DB metadata/reference commit. DB rollback orphan file bırakabilir; reconciliation/cleanup bunu yönetir. Committed DB row'un hiç oluşmamış file'a işaret ettiği ters sırayı kullanma.

QR yalnız `Material`, `SerializedAsset` veya `Location`a çözülür. Scan authentication, permission veya inventory validation'ı bypass edemez. Unknown/invalid QR state değiştirmez ve anlaşılır hata verir. Payload formatı **TBD**'dir; vendor lock-in oluşturma.

QR resolver authenticated olmalı; unrestricted enumeration/IDOR endpoint olamaz. Gerekirse QR feature'da Redis eklemeden application/proxy rate limiting değerlendirilebilir.

## 19. Audit, Security ve Logging

Inventory ledger ile administrative `AuditEvent` ayrıdır. AuditEvent; master change, correction decision, import, baseline/cutover ve permission change gibi meaningful non-stock olaylar içindir. Her ledger transaction'ı ikinci kez gereksiz audit kaydı olarak kopyalama.

Zorunlu güvenlik:

- Django CSRF
- server-side authorization
- input validation
- default safe template escaping
- ORM parameterization
- Git'te secret yok
- secure production configuration/cookies
- controlled file upload
- arbitrary ledger/stock edit yok
- correction/count/attachment gibi nesnelerde gerekli object/state-level authorization
- `ModelForm`larda explicit allowed field list; privileged/state alanları automatic inclusion ile expose etmemek
- Excel import'ta file/row/sheet/dimension ve memory/time sınırı, real workbook validation ve uygun safe read-only parsing
- Excel export'ta user-controlled `=`, `+`, `-`, `@` başlangıçlı hücreleri formula injection'a karşı nötralize etmek

Loglama: application/security/import failure ve correlation ID gibi gerekli bilgileri kaydet; password, secret, session/cookie içeriği, file binary veya gereksiz hassas payload loglama.

## 20. Testing

Her logical feature ilgili automated testleri içermelidir.

Öncelik:

> business invariants > transaction integrity > permissions > user flows > visual/UI details

Critical stock/concurrency değişikliklerini gerçek PostgreSQL integration testleriyle doğrula. En az:

- negative stock
- concurrent last-stock
- idempotency ve conflicting payload
- serialized single-location
- unauthorized receipt
- immutable correction history ve double approval
- count'un stock'u sessiz değiştirmemesi
- import'un reconciliation'ı bypass edememesi
- inactive location rejection
- concurrent first `StockBalance` row creation
- same operation ID + same/different fingerprint
- ledger DB immutability guard against bulk update/delete
- ledger-vs-projection consistency
- import commit creates zero stock ledger/balance
- count-session movement race after `DEC-HG-001` resolution
- scoped baseline retry/idempotency
- malformed/oversized workbook and spreadsheet formula injection

İlgili test başarısızsa görevi tamamlanmış sayma.

## 21. Development Workflow ve Git

Her implementation görevinde:

1. `AGENTS.md`, `docs/06-DECISION-REGISTER.md` ve ilgili docs'u oku; feature hard gate'i varsa çözülmeden dur.
2. Mevcut implementation'ı incele.
3. Değişecek dosya/alanları belirt.
4. Scope'u istenen görevle sınırla.
5. Uygula ve ilgili testleri çalıştır.
6. Değişen dosyaları, migrations'ı, dependencies'i, testleri ve kalan riskleri raporla.
7. Sonraki roadmap görevini otomatik başlatma.

Unrelated refactor yapma; “tüm projeyi iyileştir” gibi sınırsız scope kullanma.

Git zorunludur:

- Küçük, logical commit'ler.
- Unrelated feature'ları birleştirme.
- Bir logical task, review sonrası bir logical commit.
- Hataları gizlemek için history rewrite/remove yapma.
- Secret, local DB, credential veya production media commit etme.

## 22. Language ve Naming

- Code/domain identifier: **English**
- User-visible text: **Turkish**
- Documentation: **Turkish**, gerektiğinde stable English entity names

Örnek canonical entity adları: `Material`, `SerializedAsset`, `InventoryTransaction`, `StockBalance`, `Location`, `CorrectionRequest`.

Code entity adlarını rastgele Türkçe/İngilizce arasında değiştirme.

## 23. TBD Discipline

Belgelenmiş `TBD` için business cevabı uydurma. İlgili implementation bloke olursa o kısmı durdur ve şunları raporla:

1. Gerekli kesin karar.
2. Neden bu feature'ın buna bağlı olduğu.
3. Varsa güvenli seçenekler ve etkileri.

Scope izin veriyorsa bağımsız işi sürdürebilirsin. `PROPOSED` kuralı açık onay olmadan mandatory yapma. Teknik safe default, business semantics'i değiştirmiyorsa açıkça “technical default” olarak kaydedilebilir.

Legacy `TBD-*`, `OD-*`, `DM-B*` ve `UF-O-*` kimliklerinin güncel status/owner/required-before eşlemesi `docs/06-DECISION-REGISTER.md`dedir. Audit finding `AUD-*` kimliklerini Product/Business Rules içindeki aynı prefix'li legacy rule'lardan ayırmak için `Gate0-AUD-*` bağlamını kullan.

## 24. Production, Backup ve Dokümantasyon Güvenliği

Açık onay olmadan yapma:

- destructive DB operation veya production data deletion
- ledger deletion
- mass stock adjustment
- core constraint disable
- migration reset veya production DB replacement
- evidence/media deletion
- reconciliation veya permission bypass

Backup en az PostgreSQL, media/photos ve configuration dokümantasyonunu kapsar. Erken V1'de file deletion/retention yokken sıra consistent PostgreSQL backup/snapshot → media backup olmalıdır; file-before-DB metadata invariant'ı restored DB'nin referans verdiği file'ların media kopyasında bulunmasını sağlar. File deletion başladığında coordinated snapshot veya maintenance/quiesce kullan.

Restore test edilmeden backup stratejisi tamamlanmış değildir. Pilot/production öncesi restore procedure ve drill; ledger integrity, projection consistency, attachment row→file existence, missing/orphan report ve mümkünse bağımsız son transaction reference/time kontrolünü içermelidir.

Authoritative docs'u sessizce değiştirme. Dokümantasyon tutarsızlığını ayrı görev olarak raporla. Task 0.9 Gate 0 remediation, ardından bağımsız re-audit gelir.

## 25. Agent Completion Checklist

Değişiklik tesliminden önce doğrula:

- Modüler monolit ve module ownership korundu.
- Inventory mutation yalnız service layer'dan geçti.
- Ledger immutable; StockBalance doğrudan edit edilmedi.
- Committed ledger DB-level immutability guard'ı korundu.
- Source azalır / target artar semantiği ve `can_hold_stock` korundu.
- Negative stock, concurrency ve idempotency korundu.
- Nonexistent balance unique+conflict+lock ve `operation_id + request_fingerprint` contract'ı korundu.
- Quantity/serialized ve condition/movement ayrımları korundu.
- Tracking mode history sonrası değişmedi; cross-table DB guard gereksinimi korundu.
- Permission server-side uygulandı.
- Correction/count/import/baseline bypass oluşturulmadı.
- Admin, upload, QR ve audit kuralları korundu.
- DB + media backup etkisi değerlendirildi.
- Candidate data stok/ledger olmadı; opening stock yalnız scoped `INITIAL_BALANCE` ile bir kez oluştu.
- İlgili Decision Register hard gate'i açıkken feature başlatılmadı.
- Projection verify/rebuild contract bypass edilmedi.
- İlgili testler geçti.
- Yeni dependency/migration açıkça raporlandı.
- TBD uydurulmadı.
- Scope dışı dosya/refactor/task başlatılmadı.
