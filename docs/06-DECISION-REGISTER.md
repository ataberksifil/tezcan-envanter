# Decision Register — Elektrik Atölyesi Envanter Sistemi

## 1. Amaç ve Durum Tanımları

Bu belge, Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi için karar durumlarının, hard gate'lerin ve Gate 0 mimari audit bulgularının tek kanonik kaydıdır. Ürün gereksinimleri ve iş kuralları sırasıyla `docs/00-PRODUCT.md` ve `docs/01-BUSINESS-RULES.md` içinde yetkili olmaya devam eder. Bu register, bu belgelerdeki onaylı iş gerçeklerini değiştirmez; açık kararların hangi feature'ı bloke ettiğini ve daha önceki kimliklerle ilişkisini gösterir.

Durumlar:

| Status | Anlam |
|---|---|
| `DECIDED` | Mimari veya iş kararı kesinleşmiştir; ilgili uygulama bu karara uymalıdır. |
| `OPEN` | Karar verilmemiştir; `Required Before` alanındaki noktaya kadar açık kalabilir. |
| `PROPOSED` | Öneridir; açık onay olmadan zorunlu iş kuralına veya DB constraint'e dönüştürülemez. |
| `DEFERRED_WITH_HARD_GATE` | Bugün çözülmemiştir; belirtilen feature/schema/service başlamadan önce çözülmesi zorunludur. |
| `IT_DEPENDENCY` | İş anlamını değil, production deployment/operasyon yöntemini etkileyen dış bağımlılıktır. |

Audit disposition'ları:

| Disposition | Anlam |
|---|---|
| `ACCEPT_NOW` | Problem ve bu register'daki çözüm şimdi kabul edilmiştir. |
| `ACCEPT_WITH_DIFFERENT_SOLUTION` | Problem kabul edilmiş, audit'te önerilenden farklı kanonik çözüm seçilmiştir. |
| `DEFER_WITH_HARD_GATE` | Problem kabul edilmiş; iş/operasyon kararı uydurulmadan ilgili feature öncesine hard gate konmuştur. |
| `REJECT_WITH_REASON` | Bulgu gerekçeyle reddedilmiştir. Gate 0 remediation'da bu disposition kullanılmamıştır. |

### Kimlik namespace notu

Task 0.8 mimari audit bulguları bu belgede `Gate0-AUD-001`–`Gate0-AUD-018` olarak anılır; audit'in özgün `AUD-001`–`AUD-018` kimlikleri Bölüm 5'te aynen korunur. `docs/00-PRODUCT.md` ve `docs/01-BUSINESS-RULES.md` içindeki legacy `AUD-*` requirement/rule kimlikleri farklı bir namespace'e aittir ve yeniden numaralandırılmamıştır. Bağlamsız `AUD-xxx` referansı kullanılmamalıdır.

## 2. Gate 0 Mimari Kararları

### DEC-001 — Candidate inventory staging-only'dir

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-002`
- **Decision:** Excel import commit'i `StockBalance` veya stok değiştiren `InventoryTransaction` oluşturamaz. İçe aktarılan miktar, karşılaştırma ve sayım hazırlığı için `ImportRow.mapped_data` veya eşdeğer staging metadata içinde kalabilir.
- **Invariant:** **CANDIDATE INVENTORY IS NOT LEDGER DATA AND IS NOT STOCK.**
- **Consequence:** Import commit kontrollü master-data kayıtları oluşturabilir; başlangıç stoğu oluşturamaz.

### DEC-002 — Açılış stoğunun tek otoritesi INITIAL_BALANCE'tır

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-002`, `Gate0-AUD-016`
- **Decision:** Fiziksel sayım ve mutabakat sonrasında gerçek açılış stoğu tam bir kez, bir `InventoryBaseline`a bağlı `INITIAL_BALANCE` ledger işlemleriyle oluşturulur.
- **Consequence:** Import miktarı ile baseline miktarının ayrı stok etkileri olarak iki kez sayılması yapısal olarak yasaktır.

### DEC-003 — Kayıt satırı yön semantiği

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-014`
- **Decision:** Her `InventoryTransactionLine` için `source_location` o lokasyondaki stok/durumu azaltır; `target_location` o lokasyondaki stok/durumu artırır. İşlem türüne özgü istisna yoktur.
- **Constraint:** İki alan da doluysa `source_location != target_location`; DB row check ve service validation birlikte uygulanır.
- **Consequence:** İşlem türü yalnız hangi source/target kombinasyonunun geçerli olduğunu belirler. `RETURN` kombinasyonu iş kararı verilene kadar implement edilemez.

### DEC-004 — Stok tutabilen lokasyon yeteneği

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-003`
- **Decision:** `Location.can_hold_stock` açık bir capability'dir. Fiziksel stok yalnız `active = true` ve `can_hold_stock = true` lokasyonda tutulabilir.
- **Consequence:** `can_hold_stock`, leaf olma, çocuk sayısı, ad, hiyerarşi derinliği veya spekülatif `location_type` etiketinden türetilmez. Parent ve child bağımsız olarak stok tutabilir. Geçmiş referanslar sonraki pasifleştirmeden etkilenmez. Phase 3 şekil, kod, hiyerarşi ve yaşam döngüsü `DEC-023` ile kararlıdır.

### DEC-005 — Committed ledger için DB-level immutability guard

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-004`
- **Decision:** Committed `InventoryTransaction` ve `InventoryTransactionLine` üzerinde olağan `UPDATE`/`DELETE`, PostgreSQL DB-level guard ile engellenir. Tercih edilen yön, daha sonra Django migration ile yönetilecek trigger'lardır.
- **Consequence:** Admin, `queryset.update`, `bulk_update` ve raw application SQL korumayı aşamaz. İstisnai migration/repair yolu explicit, privileged, documented ve audited olmalıdır; normal uygulamada kalıcı bir disable flag bulunamaz.

### DEC-006 — Var olmayan StockBalance satırının güvenli oluşturulması

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-005`
- **Decision:** `SELECT FOR UPDATE` var olmayan satırı kilitleyemez. Yeni `(material_id, location_id, condition_id)` anahtarı için composite `UNIQUE`, safe insert/on-conflict, canonical satırı tekrar okuma ve row lock zorunludur.
- **Sequence:** `BEGIN → ensure row with conflict handling → SELECT FOR UPDATE → re-read → validate → ledger → projection → COMMIT`.
- **Consequence:** Savepoint + `IntegrityError`, PostgreSQL `ON CONFLICT` veya eşdeğer güvenli yöntem implementation review'da seçilebilir.

### DEC-007 — READ COMMITTED ve lock-then-revalidate

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-005`
- **Decision:** Production transaction varsayımı PostgreSQL default `READ COMMITTED`dır. Inventory service önce gerekli satırları kilitler, sonra current state'i yeniden okuyup bütün invariant'ları doğrular.
- **Consequence:** Lock öncesi form/service pre-validation kullanıcı deneyimi içindir; correctness otoritesi değildir.

### DEC-008 — Deterministic lock order

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-005`
- **Decision:** Çoklu quantity balance kilit sırası `material_id → location_id → condition_id → primary key` şeklindedir. Serialized işlem `SerializedAsset` satırını kilitler ve state'i kilit sonrasında doğrular.
- **Consequence:** Beklenmeyen deadlock/DB transaction hatası tam rollback üretir; kısmi ledger/projection etkisi olamaz.

### DEC-009 — operation_id ve server-generated request_fingerprint

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-007`
- **Decision:** Her inventory-changing command `operation_id UUID` ve semantic command payload'un canonical server representation'ından hesaplanan SHA-256 hexadecimal `request_fingerprint` kullanır. Client fingerprint'i güvenilmez.
- **Includes:** İşlem türü, gerekli actor/context, material/asset, miktar, kondisyon, source/target ve diğer semantic alanlar.
- **Excludes:** `operation_id`, server timestamp, presentation-only değerler ve canonical sorting sonrası semantik olmayan sıra.
- **Behavior:** Aynı ID + aynı fingerprint önceki başarılı sonucu verir; aynı ID + farklı fingerprint conflict üretir; iki durumda da ikinci stok etkisi yoktur. Eşzamanlı aynı-ID isteklerde DB uniqueness arbiter'dır.

### DEC-010 — Workflow-owned ilişki ve bağımlılık yönü

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-009`
- **Decision:** `corrections`, `counting` ve `imports` inventory'ye bağımlı olabilir; `inventory` bu downstream workflow modüllerini import edemez. Sonuç transaction bağlantısını workflow sahibi tutar.
- **Consequence:** `InventoryTransaction.correction_request_id` ve `InventoryTransaction.inventory_baseline_id` yoktur. `source_transaction_id` inventory içinde self-reference olarak kalabilir. Aynı ilişki iki yönde redundant FK ile tutulamaz.

### DEC-011 — Neutral identification boundary

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-009`
- **Decision:** `BarcodeIdentifier`, `Material`, `SerializedAsset` ve `Location` referansları nedeniyle neutral `identification` feature boundary'sine aittir.
- **Consequence:** `identification`, `catalog`, `inventory` ve `locations`a bağımlı olabilir; bu modüller core domain işlemleri için `identification`a bağımlı olmaz. Django app QR feature başladığında oluşturulur.

### DEC-012 — Quantity/serialized cross-table integrity

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-011`
- **Decision:** Aşağıdaki catastrophic invariant'lar service validation ve uygun DB-level integrity guard ile korunur:
  1. `SerializedAsset.material.tracking_mode = SERIALIZED`.
  2. `StockBalance`, `SERIALIZED` material için var olamaz.
  3. Serialized line için `line.material_id = serialized_asset.material_id`.
  4. Quantity ve serialized line şekilleri karışamaz.
- **Different solution:** Sırf composite FK simülasyonu için redundant `tracking_mode` kolonları şimdi kabul edilmemiştir. Row-local check/constraint kullanılır; kritik cross-table kurallarda targeted PostgreSQL constraint trigger/guard implementation review'da belirlenir.

### DEC-013 — History sonrası tracking_mode immutable'dır

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-011`
- **Decision:** Bir `Material` herhangi bir inventory ledger history'ye sahip olduktan sonra `tracking_mode`, normal uygulama yollarında değiştirilemez.
- **Consequence:** Gelecekte migration gerekiyorsa ayrı, kontrollü ve açık iş kararlı veri dönüşüm projesidir; olağan material edit'i değildir.

### DEC-014 — Projection verify/rebuild contract

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-008`
- **Decision:** `StockBalance` ve serialized current state projection'dır. `verify_inventory_projection` benzeri read-only operasyon ledger'dan beklenen state'i temporary/in-memory olarak üretip persisted projection ile karşılaştırmalı ve fark raporlamalıdır.
- **Repair:** Rebuild/repair ayrı, explicit privileged, audit edilen, varsayılan dry-run ve mümkünse backup sonrası kullanılan bir operasyondur; otomatik çalışmaz.
- **Gate:** Inventory engine gate'inden ve kesinlikle pilot'tan önce verify capability ve ledger-vs-projection automated tests zorunludur.

### DEC-015 — Scoped INITIAL_BALANCE işlemleri

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-016`
- **Decision:** Bir `InventoryBaseline`, downstream-owned link üzerinden bir veya daha çok scoped `INITIAL_BALANCE` transaction'a sahip olabilir.
- **Invariant:** Her link yalnız `INITIAL_BALANCE` type'a gider; her scope idempotent'tır; baseline ancak bütün gerekli scope'lar commit edilip doğrulandıktan sonra `ESTABLISHED` olur; aynı cutover context için en fazla bir authoritative `ESTABLISHED` baseline bulunur.

### DEC-016 — Django Admin protected-model policy

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-012`
- **Decision:** Ledger, line, `StockBalance` ve serialized current-state protected modellerde ordinary Admin add/change/delete yoktur; transaction-line inline ve destructive bulk action yoktur; readonly alanlar explicit'tir.
- **Consequence:** Stock-changing admin action aynı inventory service/workflow'u kullanır. Admin lockdown, DB guard'ın yerine geçmez; shell/dbshell/management command de DB invariant'larını aşamaz.

### DEC-017 — System-specific security controls

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-013`
- **Decision:** Sensitive attachment erişimi permission-checked application path ile ve object-level authorization altında yapılır; public `MEDIA_URL` kanıt erişim yolu değildir. Import'ta file/row/sheet/bellek-zaman limitleri, content doğrulama ve safe read-only parsing uygulanır. Excel export'ta `=`, `+`, `-`, `@` ile başlayan user-controlled text formula injection'a karşı nötralize edilir. `ModelForm` allowed field listeleri explicit'tir.
- **Consequence:** Role authorization'a ek olarak object/state authorization uygulanır. QR resolve authenticated'tır ve enumeration/permission bypass olamaz; QR fazında gerekirse Redis'siz rate limiting değerlendirilir.

### DEC-018 — File, backup ve restore consistency contract

- **Status:** `DECIDED`
- **Audit:** `Gate0-AUD-015`
- **Decision:** File write sırası `validate/upload → persistent storage'da finalize → DB metadata/reference commit` şeklindedir. DB rollback sonrası orphan file olabilir; committed metadata'nın hiç oluşmamış dosyaya işaret etmesi engellenir.
- **Different solution:** Erken V1'de deletion/retention cleanup aktif değilken backup sırası `consistent PostgreSQL backup/snapshot → media backup`tır. File-before-DB invariant'ı nedeniyle restored DB'nin referans verdiği dosyalar sonraki media kopyasında bulunur; daha yeni orphan extra file zararsızdır.
- **Future:** File deletion/retention başladığında coordinated snapshot veya maintenance/quiesce gerekir.
- **Restore gate:** Drill; ledger integrity, projection consistency, attachment-row→file existence, orphan/missing report ve mümkünse bağımsız son transaction reference/time kontrolünü içermelidir.

### DEC-019 — Project-owned AUTH_USER_MODEL from first migration

- **Status:** `DECIDED`
- **Required before:** İlk Django migration
- **Decision:** `AUTH_USER_MODEL`, ilk Django migration/bootstrap'tan itibaren minimal proje sahipli `accounts.User` modeline (`AbstractUser` tabanlı) işaret eder. Başlangıçta spekülatif employee/iş alanları eklenmez; `Employee` ayrı domain kavramı olarak kalır.
- **Reason:** Django default `User` modelinden sonradan custom `User`'a geçiş, migration ve bağımlı tablolar oluştuktan sonra gereksiz yere yıkıcıdır. Minimal `AbstractUser` alt sınıfı, employee business semantiğinin ayrı `Employee` entity'sinde kalması için gelecek seçenekleri korur (`DEC-024`).
- **Consequence:** `django.contrib.auth.models.User`'a geri dönülmez. `Employee` ile `User` birleştirilmez. Employee foundation `DEC-024` ile kararlıdır; employee business alanları `User`'a taşınmaz.

### DEC-020 — Teknisyen saha/atölye alım talebi ve onay zorunluluğu

- **Status:** `DECIDED`
- **Supersedes:** Phase 2.3 ve öncesindeki `AUTH-003A` ifadeleri ile Teknisyen'in sahadan/atölyeye fiziksel getirilen malzeme için otoritatif stok girişini doğrudan kaydedebileceğini ima eden tüm wording.
- **Decision:**
  1. `TECHNICIAN` satın alma/tedarikçi teslimatı kabulü veya olağan supplier/purchase `RECEIPT` asla yapamaz (`RCV-002`).
  2. Uygun saha veya sahada kullanılan alan → atölye senaryolarında `TECHNICIAN` yalnızca talep başlatabilir (`AUTH-003A`, `INT-001`).
  3. Her Teknisyen başlatımlı talep, otoritatif envanter etkisi öncesinde `ADMIN_MANAGER` onayı gerektirir (`AUTH-012`, `INT-002`).
  4. Talep `PENDING` iken `InventoryTransaction`/ledger, `StockBalance` artışı ve `SerializedAsset` state/lokasyon mutation oluşmaz (`INT-003`).
  5. Reddedilen talep envanter etkisi oluşturmaz; talep ve kanıt ileride workflow uygulandığında tarihsel izlenebilir kalır (`INT-004`).
  6. Onaylanan talebin envanter etkisi yalnızca gelecekteki otoritatif envanter servisi üzerinden atomik ve idempotent olarak gerçekleşir (`INT-005`).
  7. Kavramsal senaryolar (hareket türü eşlemesi yapılmaz): tamamen kullanılmamış geri getirme; kısmen kullanılmamış geri getirme; yanlış alınmış kullanılmamış iade; kullanılmış/sökülmüş malzeme; arızalı/sökülmüş malzeme; orijinal depo `ISSUE` kaydı bilinmeyen fabrika sahası malzemesi.
  8. Daha önce çıkış yapılmış kullanılmamış malzeme orijinal kondisyonuyla geri gelebilir; uygunluk, miktar limiti, tekil kimlik, provenans ve kondisyon geçişleri `DEC-HG-005` kapsamında açık kalır (`INT-006`).
  9. `STOREKEEPER` olağan supplier/purchase receipt yetkisi bu kararla değişmez (`RCV-001`, `DEC-OPEN-005`).
  10. `RETURN`, `RECEIPT`, `TRANSFER`, `CONTROLLED_CORRECTION` veya yeni hareket türüne önceden eşleştirme yapılmaz; `DEC-HG-005` hard gate korunur.
- **Consequence:** Talep/onay workflow schema, service, UI, permission tabloları ve envanter kodu bu kararın implementasyon görevi olarak ayrıca tanımlanır; bu register kaydı tek başına implementasyon başlatmaz.

### DEC-021 — Dynamic Configuration Architecture

- **Status:** `DECIDED`
- **Required before:** Phase 2 catalog/configuration UI ve dinamik rol yönetimi implementasyonu
- **Principle:** Envanter doğruluğunu tehlikeye atmadan güvenle yönetici tarafından yönetilebilecek her şey dinamik/yapılandırılabilir olmalıdır; hard-coded olmamalıdır.
- **Decision:**
  1. **Dynamic master data:** Aşağıdaki referans/master veriler ilgili implementasyon fazına geldiğinde UI ile yönetilecek şekilde tasarlanır: Category hiyerarşisi, `UnitOfMeasure`, `Material`, Location hiyerarşisi, `ProductionLine`, onaylı reason/reference listeleri, technical-field tanımları ve yapılandırılabilir eşikler. Normal eklemeler kod veya migration gerektirmemelidir. Seed değerleri başlangıç varsayılanlarıdır; kapalı whitelist veya korumalı iş kaydı değildir.
  2. **Dynamic roles:** `TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER` başlangıç rol şablonlarıdır; sistemin gelecekte sahip olabileceği tek roller değildir. Gelecekte yöneticiler ek roller oluşturabilir ve onaylı izinleri kontrollü uygulama UI üzerinden yönetebilir. Runtime authorization permission/policy tabanlı olmalıdır; hard-coded Group adı kontrolü kullanılmamalıdır.
  3. **Phase 2 role-management boundary:** Phase 2 dinamik rol yönetimi yalnızca güvenli catalog/configuration izinlerini expose edebilir. Inventory receipt/issue/approval izinleri, ilgili inventory hard gate'leri tasarlanana kadar dinamik olarak expose edilmez. Gerekçe: Django Group permission'ları additive'tir ve onaylı Teknisyen supplier-receipt yasağı (`DEC-020`, `RCV-002`) multi-group membership ile yanlışlıkla bypass edilebilir.
  4. **`setup_roles` contract:** Gelecekteki varsayılan davranış non-destructive olmalıdır: varsayılan rol yoksa oluşturulur ve başlangıç şablon izinleri atanır; rol zaten varsa permission ekleme/çıkarma/reconcile yapılmaz. Deployment sonradan yapılan yönetici değişikliklerini sessizce overwrite etmemelidir. Açık recovery/reset davranışı varsa normal deployment yolu olmamalıdır (Phase 2.5C).
  5. **`UnitOfMeasure` policy (Phase 2.6):** `code` yetkili yönetici tarafından düzenlenebilir kalır; UUID kararlı kimliktir; `code` uniqueness korunur; `code` değişiklikleri audit edilir; case-insensitive normalization kuralı uydurulmaz; `decimal_places` semantik olarak `DEC-OPEN-010` altında çözülmemiş kalır; rounding/conversion davranışı oluşturulmaz. Referanslı bir UoM deaktive edilebilir: mevcut referanslar korunur, Material'lara cascade/mutation yapılmaz, yeni atama UI'larında inactive UoM sunulmaz, tarihsel/mevcut referanslar geçerli kalır. Seed UoM'ler özel koruma almaz.
  6. **Concurrency:** Phase 2 catalog master data için optimistic locking/`state_version` zorunlu değildir; geçici davranış last-write-wins kalır. Bu, gelecekteki stok mutation concurrency'sine uygulanmaz; stok tarafı transactional/locked kalır.
  7. **Technical specifications:** `Material.technical_specs` unrestricted raw JSON editor olarak expose edilmez. Gelecekteki kategori-özel teknik alanlar controlled `TechnicalFieldDefinition`-style metadata ile yönetilir: stable key, display label, closed data type, required flag, optional unit/choice metadata, ordering, active/inactive, safe bounded validation metadata. Arbitrary Python/SQL/plugin execution yoktur. Exact definition modeli sonraki gate'e bırakılır (`DEC-OPEN-019`).
  8. **Location:** Gelecekteki Location hiyerarşisi dinamik ve arbitrary-depth'tir. Hard-coded warehouse/corridor/rack/bin schema seviyeleri zorunlu değildir. `can_hold_stock` presentation/type label'larından ayrı kalır. Location implementasyonu Phase 2 dışındadır. Phase 3.0 foundation `DEC-023` ile kararlıdır; implementasyon Phase 3.1'dir.
  9. **Hard invariant boundary:** Aşağıdakiler yönetici tarafından devre dışı bırakılabilir configuration haline gelemez: negatif stok yasağı; immutable `InventoryTransaction` ledger; immutable `AuditEvent`; yalnız inventory-service mutation; atomik stok mutation; idempotency; Decimal quantity semantics; doğrudan `StockBalance` edit yasağı; serialized identity integrity; `DEC-020` pending Teknisyen intake'in otoritatif stok etkisi olmaması; diğer onaylı inventory hard gate'ler. Movement mathematics generic configuration ile oluşturulamaz.
  10. **Movement / condition boundary:** Movement semantic type'lar code-controlled kalır. Condition label/reference metadata gelecekte dinamik olabilir; availability effect ve allowed transition'lar code/policy controlled kalır. `RETURN` ve Teknisyen movement classification çözülmez (`DEC-HG-005`, `DEC-020`).
  11. **Audit:** Her başarılı dynamic configuration mutation sonunda `permission → service → transaction → mutation + AuditEvent` yolu kullanılmalıdır. No-op veya başarısız/reddedilen işlem başarılı configuration mutation audit event'i üretmez. Audit schema değiştirilmez.
  12. **Phase 2 roadmap:** Kanonik sıra: 2.5B Dynamic Configuration Architecture decision → 2.5C Non-destructive role bootstrap hardening → 2.6 UnitOfMeasure UI → 2.7 Material list/search/detail → 2.8A Material base writes → 2.8B Technical-specification gate → 2.9A Yönetim/configuration shell → 2.9B Dynamic roles/permissions/user assignment → 2.9C technical-field configuration (yalnız onaylı/hazır ise) → 2.10 Gate 2. Location, `ProductionLine` ve inventory implementasyonu Phase 2 dışındadır.
- **Consequence:** `DEC-020` değişmez. Seed verileri kapalı whitelist olarak ele alınmaz. Mevcut `setup_roles` reconcile davranışı Phase 2.5C ile non-destructive contract'a hizalanır. Catalog master data UI'ları permission tabanlıdır; inventory mutation izinleri Phase 2 dinamik rol yönetimine dahil edilmez.

### DEC-022 — Phase 2 Access Management Policy

- **Status:** `DECIDED`
- **Required before:** Phase 2.9B dynamic roles/permissions/user assignment implementasyonu
- **Supersedes (partial):** Phase 2.9B öncesi `AUTH-011` ve `OD-026` içindeki rol atama/onay süreci belirsizliği; inventory onay kurallarına uygulanmaz (`DEC-020` değişmez).
- **Decision:**
  1. **Role model:** Django `Group` Phase 2 rol modeli olarak kalır. `RoleProfile` veya alternatif RBAC modeli tanıtılmaz. `TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER` bootstrap şablonlarıdır; runtime authorization identity değildir. Runtime authorization permission tabanlıdır.
  2. **Management capability:** Phase 2.9B tek dar custom permission tanıtır: `accounts.manage_access`. Amaç: rol yönetimi, onaylı rol permission'ları ve kullanıcı–rol atamaları. Bu permission sıradan catalog permission'larından ayrıdır. İlk bootstrap otoritesi Django superuser'dır. Superuser, audited Phase 2.9B uygulama UI üzerinden bir role `accounts.manage_access` verebilir/alabilir. Superuser olmayan access manager `manage_access` veremez/alamaz. Group adı, `is_staff` veya `ADMIN_MANAGER` adı `manage_access` ile eşdeğer sayılmaz.
  3. **Safe Phase 2 permission allowlist:** Sıradan rol permission yönetimi yalnız şu dokuz izni expose eder: `catalog.view_category`, `catalog.add_category`, `catalog.change_category`, `catalog.view_unitofmeasure`, `catalog.add_unitofmeasure`, `catalog.change_unitofmeasure`, `catalog.view_material`, `catalog.add_material`, `catalog.change_material`. Hariç tutulanlar: tüm `delete_*` permission'ları; audit mutation permission'ları; ham auth model-management permission'ları; inventory permission'ları; correction/count/approval permission'ları; gelecek workflow permission'ları; keyfi Django `Permission` satırları. `accounts.manage_access` bu dokuzluk allowlist'in parçası değildir; ayrı korunan delegation capability'dir.
  4. **Write/view invariant:** Category, UnitOfMeasure ve Material için `add_*` veya `change_*`, karşılık gelen `view_*` olmadan yapılandırılamaz. UI yardımcı olabilir; otorite sunucu tarafı service validation'dır. Forge edilmiş input bu kuralı bypass edemez.
  5. **Role lifecycle:** Phase 2'de rol hard delete expose edilmez; active/inactive rol modeli tanıtılmaz; custom roller yeniden adlandırılabilir; varsayılan bootstrap roller `setup_roles` canonical ad ile tanındığı için yeniden adlandırılamaz; varsayılan rol permission'ları özelleştirilebilir; `setup_roles` non-destructive kalır. Kullanılmayan rol üyelikleri kaldırılabilir ve permission'ları temizlenebilir. Soft-delete framework yoktur.
  6. **User-role management boundary:** Phase 2.9B yalnız `User.groups` yönetir. Yönetmez: password, `is_active`, `is_staff`, `is_superuser`, Employee linkage, `user_permissions`. Direct `user_permissions` dokunulmaz ve management UI'da read-only kalır.
  7. **Privilege / lockout safety (non-superuser `accounts.manage_access` actor):** Actor kendi rol üyeliklerini değiştiremez; üye olduğu rollerin adını veya permission'larını değiştiremez; sahip olmadığı güvenli catalog permission'ını role veremez; `accounts.manage_access` içeren rolü değiştiremez; `accounts.manage_access` içeren rolü atayamaz/kaldıramaz; effective permission'ları `accounts.manage_access` içeren kullanıcıyı değiştiremez; superuser, `is_staff` kullanıcı, direct `user_permissions` taşıyan kullanıcı ve actor'ın desteklediği catalog permission kümesini aşan kullanıcı/rolü değiştiremez. Rank/hierarchy numarası veya deny permission tanıtılmaz. Superuser bootstrap/delegation otoritesidir; superuser hedefinin kendisi bu UI üzerinden düzenlenmez.
  8. **Approval policy:** Phase 2 rol ve kullanıcı–rol yönetimi ikinci manager onayı gerektirmez. Yetkilendirme: `accounts.manage_access` + sunucu tarafı anti-escalation kuralları + immutable `AuditEvent`. Bu karar inventory onayına uygulanmaz (`DEC-020` değişmez).
  9. **Audit identity:** `AuditEvent` şeması değişmez. Django `Group` ve `User` non-UUID PK kullanır. Phase 2.9B için kararlı audit entity UUID'leri UUIDv5 ile türetilir: roller için sabit immutable namespace, kullanıcılar için farklı sabit immutable namespace; name input olarak integer PK (`uuid5(ROLE_NAMESPACE, str(group.pk))`, `uuid5(USER_NAMESPACE, str(user.pk))`). Namespace sabitleri kaynak kodda fixed'tir; display name identity input değildir; rename audit identity'yi değiştirmez; integer PK canonical audit snapshot'ta kalır; event başına random UUID üretilmez.
  10. **Audit events:** Onaylı aileler: `accounts.role.created`, `accounts.role.updated`, `accounts.role.permissions_changed`, `accounts.user.roles_changed`. No-op audit üretmez. Reddedilen/denied başarılı mutation audit'i değildir. Mutation ve audit aynı transaction içindedir; audit failure mutation rollback üretir.
  11. **Django Admin boundary:** Phase 2.9B audited management implement edildiğinde `auth.Group` writable Admin yüzeyi kaldırılır; `accounts.User` groups/user_permissions/privilege flag'leri için writable Admin yüzeyi kazanmaz; `AuditEvent` read-only kalır. Normal rol/kullanıcı–rol yönetimi audited application service'ler üzerinden yapılır.
  12. **UI boundary:** Phase 2.9B küçük uygulama UI'dır, generic IAM değildir. Planlanan yüzeyler: Yönetim → Roller ve Yetkiler; Yönetim → Kullanıcılar. Rol: list, create, custom-role rename, safe permission management. Kullanıcı: list, user-role assignment. Yok: rol delete, direct-permission editor, password/user account editor, generic permission browser, hierarchy engine, approval engine.
  13. **Phase 3 Location permission extension:** Phase 3, DEC-022-style managed allowlist'i şu üç izinle genişletir: `locations.view_location`, `locations.add_location`, `locations.change_location`. `delete_location` application access management'ta expose edilmez. `add_location` veya `change_location`, `view_location` gerektirir (mevcut write/view invariant). Varsayılan şablonlar ileride `TECHNICIAN` ve `STOREKEEPER` için `view_location`, `ADMIN_MANAGER` için view/add/change içerebilir. `setup_roles` non-destructive kalır; mevcut Group'lar reconcile edilmez ve sessizce yeni izin almaz. Location şekli, kod ve yaşam döngüsü `DEC-023`tedir.
- **Consequence:** Phase 2.9B product-policy blocker kapanır. `AUTH-011` rol atama/onay ve Phase 2 tam yönetim sınırı kısmı kararlıdır; operasyonel depo işleri (`DEC-OPEN-005`) açık kalır. Employee–ApplicationUser ilişkisi `DEC-024` ile kararlıdır. Phase 3 Employee izinleri `DEC-024` item 8'de planlanır. `DEC-020`, inventory permission boundary, `DEC-OPEN-019` ve diğer inventory hard gate'ler değişmez. Phase 3 Location izinleri item 13 ve `DEC-023` ile allowlist'e eklenir; Phase 2 dokuz catalog izni tarihi olarak korunur.

### DEC-023 — Phase 3 Location Foundation Policy

- **Status:** `DECIDED`
- **Required before:** Phase 3.1 Location foundation implementation
- **Resolves:** Location code portion of `DEC-OPEN-021`; `DEC-OPEN-012`; `OD-016`; `DM-B09` type/hierarchy/code/deactivation; `LOC-007`; `UF-O-15`; `TBD-008` hierarchy/code/lifecycle portion
- **Does not resolve:** Material code uniqueness/format (`DEC-OPEN-021` remainder); `DEC-HG-001`, `DEC-HG-002`, `DEC-HG-005`; `DEC-OPEN-019`; inventory mutation (`ProductionLine` ve `Employee` foundation kararları Phase 3.2-0'da `DEC-024`/`DEC-025` ile kararlıdır)
- **Principle:** İş/master veri, envanter doğruluğunu tehlikeye atmadan mümkün olduğunca dinamik yapılandırılabilir ve güvenle düzenlenebilir olmalıdır. Kararlı kimlik UUID'dir; iş yüzü ad/kod ve hiyerarşi evrilebilir. Hiyerarşik entity'ler, pratik olduğu yerde sabit warehouse/shelf/line-level enum yerine dinamik recursive parent kullanır. Generic tree framework tanıtılmaz. Historical/audit/inventory gerçekleri, güncel master data sonradan düzenlense bile immutable kalır.
- **Decision:**
  1. **Minimal Location shape (Phase 3.1):** UUID primary key; `code`; `name`; nullable recursive `parent`; `active`; `can_hold_stock`; `created_at`; `updated_at`. `location_type` Phase 3.1 için zorunlu değildir ve sabit enum olarak implement edilmez.
  2. **Location code:** Zorunlu; dış boşluk trim edilir; blank yasaktır; globally unique; case-sensitive; editable; regex format yoktur; forced upper/lowercase yoktur. Kalıcı kimlik UUID'dir, `code` değildir.
  3. **Location name:** Zorunlu; trim edilir; non-unique; editable.
  4. **Hierarchy:** Dinamik ve arbitrary depth. Örnek evrim `site → workshop → warehouse → area → shelf → sub-shelf` olabilir; bu seviyeler hard-coded değildir. Root: `parent = null`. Parent sonradan değiştirilebilir. Self-parent yasaktır. Descendant/cycle parenting yasaktır. Sabit maksimum derinlik yoktur. Sabit level/type enum yoktur. Parent ve child bağımsız olarak stok tutabilir. Inactive parent'ın active child'ı olabilir. Parent status children'a cascade etmez. Bu fazda path/depth cache persist edilmez. Generic tree framework yoktur.
  5. **`location_type`:** Daha önce önerilen zorunlu `location_type` Phase 3.1'den çıkarılır/ertelenir. `WAREHOUSE`, `WORKSHOP`, `SHELF`, `BIN` gibi sabit enum değerleri implement edilmez. Phase 3.1 operasyonel capability `can_hold_stock`tır. Sınıflandırma ileride gerekirse spekülatif hard-coded enum yerine dinamik yapılandırma tercih edilir.
  6. **`can_hold_stock`:** Düzenlenebilir, bağımsız capability. Leaf status, child count, name, hierarchy depth veya type'tan türetilmez. Parent ve child ikisi de `True` olabilir. Default: `False`. Gelecekteki yeni stok yerleşimi `active=True AND can_hold_stock=True` gerektirir. Bu karar kaydı stok logic implement etmez.
  7. **Active/inactive lifecycle:** Hard delete iş operasyonu yoktur. Inactive Location okunabilir ve tarihsel olarak referanslanabilir kalır; gelecekteki YENİ stok yerleşimini alamaz. Deactivation children'a cascade etmez. Reactivation children/history mutate etmez. Yetkili envanter varken non-zero stock'lu Location pasifleştirilemez ve `can_hold_stock=True` → `False` yapılamaz; stok önce taşınmalı/mutabakatla sıfırlanmalıdır. Envanter satırları yokken Phase 3.1 Location CRUD bu kuralı stok satırı incelemeden uygulayabilir. Inventory entegrasyonu aynı invariant'ı sonradan otoritatif olarak uygulamak zorundadır.
  8. **Permissions:** Phase 3 managed izinler: `locations.view_location`, `locations.add_location`, `locations.change_location`. Application access management `delete` izni expose etmez. `add_location` veya `change_location` `view_location` gerektirir. Allowlist genişlemesi `DEC-022` item 13'tedir. `setup_roles` non-destructive kalır.
  9. **Management / audit:** Location mevcut Yönetim shell üzerinden yönetilir. Writable Django Admin Location yüzeyi yoktur. Gelecekteki Location write servisleri `transaction + service-layer mutation + AuditEvent` kullanır. Planlanan event ailesi: `locations.location.created`, `locations.location.updated`, `locations.location.deactivated`, `locations.location.reactivated`. Canonical identity Location UUID'dir. Delete operasyonu olmadığı için delete event yoktur.
  10. **Seed data:** Tahmin edilmiş fabrika Location satırları seed edilmez. Elektrik Deposu, Alkali Elektrik, Enstrüman Atölyesi, Bobinaj Atölyesi gibi bilinen adlar örnek / gerçek dünya girdisidir; kesin hiyerarşi, kod, `can_hold_stock` ve child yapısı henüz yeterince tanımlı değildir. Onaylandığında yönetim UI'sı üzerinden dinamik oluşturulurlar.
  11. **Phase 3 roadmap:** Phase 3.0 Location foundation decisions COMPLETE. Phase 3.1 Location foundation implementation COMPLETE. Inventory başlamamıştır.
- **Consequence:** Phase 3.1 Location CRUD `DEC-023` şekline uyar. Material code, ProductionLine ve Employee foundation kararları Phase 3.2-0'da (`DEC-024`, `DEC-025`); inventory hard gate'leri (`DEC-HG-001`, `DEC-HG-002`, `DEC-HG-005`) açık kalır.

### DEC-024 — Phase 3 Employee Foundation Policy

- **Status:** `DECIDED`
- **Required before:** Phase 3.2 Employee foundation implementation
- **Resolves:** `DEC-HG-004` (employee identity foundation); employee number portion of `DEC-OPEN-021`; `DM-B01`, `DM-B08`, `UF-O-02`, `UF-O-16`, `UF-O-17` (employee linkage/number); Gate0-AUD-017 (identity foundation portion)
- **Does not resolve:** Personal/photo/audit retention (`DEC-OPEN-013`, `DEC-OPEN-018`); Material code remainder (`DEC-OPEN-021`); inventory mutation; ISSUE implementation
- **Principle:** İş/master veri dinamik yapılandırılabilir olmalıdır. Kararlı kimlik UUID'dir; sicil numarası ve ad/soyad düzenlenebilir. Historical transaction/audit snapshot'ları immutable kalır.
- **Decision:**
  1. **Entity:** `accounts.Employee` ayrı dedicated entity'dir. `accounts.User` authentication/authorization actor; `Employee` business/physical receiver identity. İkisi de birbirinin varlığını gerektirmez; birleştirilmez.
  2. **Minimal shape (Phase 3.2):** UUID PK; `employee_number`; `first_name`; `last_name`; nullable one-to-one `User` link (ownership `Employee` tarafında); `active`; `created_at`; `updated_at`.
  3. **`employee_number`:** Zorunlu; string; leading zero korunur; numeric-only constraint yok; regex format yok; outer trim; case preserved; globally unique (PostgreSQL case-sensitive); editable; UUID kalıcı kimliktir. Numara değişince Employee kimliği değişmez. Eski numara Phase 3.2'de ayrı historical registry ile rezerve edilmez; teknik olarak başka aktif Employee tarafından yeniden kullanılabilir. Tarihsel doğruluk immutable transaction snapshot'larıyla korunur. Import/matching logic `employee_number`'ı immutable identity key varsaymamalıdır. `EmployeeNumberHistory`/alias tablosu şimdi yoktur.
  4. **User link:** `Employee.user` nullable one-to-one → `accounts.User`; delete behavior `SET_NULL`. Bir User en fazla bir Employee; bir Employee en fazla bir User. Link düzenlenebilir; permission/rol vermez; `TECHNICIAN` bir Employee type değildir. `Employee.active` ve `User.is_active` bağımsızdır; biri diğerini otomatik mutate etmez.
  5. **Lifecycle:** Hard-delete application surface yok; active/inactive; create active; inactive okunabilir kalır; inactive yeni ISSUE receiver seçilemez; reactivation UUID/history korur; status değişimi explicit. Normal editable: `employee_number`, `first_name`, `last_name`, user link. Protected: UUID, `active` (normal edit form üzerinden), timestamps.
  6. **Audit:** Gelecek Employee write'ları immutable `AuditEvent` kullanır. Planlanan aile: `accounts.employee.created`, `accounts.employee.updated`, `accounts.employee.deactivated`, `accounts.employee.reactivated`.
  7. **Future ISSUE contract:** Receiver `Employee` UUID referansı; transaction `employee_number`, `first_name`, `last_name` snapshot'lar; sonraki Employee edit historical ISSUE'yu rewrite etmez; inactive Employee yeni ISSUE için kullanılamaz. ISSUE henüz implement edilmez.
  8. **Permissions:** Phase 3 managed: `accounts.view_employee`, `accounts.add_employee`, `accounts.change_employee`. `delete_employee` expose edilmez. `add_employee` ve `change_employee`, `view_employee` gerektirir. User-link editing `change_employee` kapsamındadır; `manage_access` gerekmez.
  9. **Default role templates (yalnız NEW/MISSING bootstrap Groups):** TECHNICIAN + `view_employee`; STOREKEEPER + `view_employee`; ADMIN_MANAGER + view/add/change. `setup_roles` non-destructive; mevcut Group'lar reconcile edilmez.
  10. **Phase 3 roadmap:** Phase 3.2-0 Employee + ProductionLine decision pack COMPLETE. Sıradaki: Phase 3.2 Employee foundation implementation. Employee henüz implement edilmemiştir.
- **Consequence:** `DEC-HG-004` employee foundation kısmı kapanır. Employee implementation Phase 3.2'de `DEC-024` şekline uyar. Retention pilot öncesi kararları açık kalır.

### DEC-025 — Phase 3 ProductionLine Foundation Policy

- **Status:** `DECIDED`
- **Required before:** ProductionLine foundation implementation (Phase 3.2 sonrası)
- **Resolves:** `DEC-HG-003` (ProductionLine foundation); `OD-007`, `DM-B07`, `UF-O-01` (production line structure); Gate0-AUD-010 (controlled reference structure)
- **Does not resolve:** ISSUE schema/service/UI; exact `UsagePlace` model; inventory mutation; `DEC-HG-005`
- **Module owner:** `inventory` app (ProductionLine master data).
- **Principle:** Dinamik master data; UUID kararlı kimlik; code/name editable; recursive hierarchy preferred; Location hiyerarşisinden ayrı domain yapısı; generic tree framework yok.
- **Decision:**
  1. **Entity:** `ProductionLine` gerçek dynamic master-data entity'dir. Minimal shape: UUID PK; `code`; `name`; nullable recursive `parent`; `active`; `created_at`; `updated_at`. Henüz implement edilmez.
  2. **Hierarchy:** Dinamik, arbitrary depth (ör. Plant → Line → Section → Sub-section). `parent` nullable; editable/reparentable; self-parent yasak; cycle yasak; fixed type enum yok; max depth yok; path/depth cache yok. Location hiyerarşisine bağlanmaz; ayrı domain yapılarıdır.
  3. **`code`:** Zorunlu; outer trim; blank yasak; globally unique; case-sensitive; editable; regex/forced case yok; UUID gerçek kimlik.
  4. **`name`:** Zorunlu; trimmed; editable; non-unique.
  5. **Lifecycle:** Active/inactive; hard-delete application surface yok; inactive tarihsel okunabilir; inactive yeni ISSUE seçiminde kullanılamaz; status children'a cascade etmez.
  6. **Future ISSUE contract:** Seçilen `ProductionLine` UUID referansı; transaction `code`/`name` snapshot'lar. ISSUE henüz implement edilmez.
  7. **Exact usage place:** V1 future ISSUE ayrı required free-text exact usage-place değeri gerektirir. `ProductionLine` structured selectable context; exact usage place ayrı kalır. `UsagePlace` modeli şimdi yoktur. Location veya ProductionLine'dan infer edilmez.
  8. **Permissions (planlanan):** `inventory.view_productionline`, `inventory.add_productionline`, `inventory.change_productionline`. `delete` expose edilmez. Write requires view. Yönetim shell üzerinden dinamik yönetim. Bu docs görevinde permission kodu değiştirilmez.
  9. **Seed:** Tahmin edilmiş fabrika hatları seed edilmez.
  10. **Phase 3 roadmap:** ProductionLine foundation implementation Employee'den sonra gelir.
- **Consequence:** `DEC-HG-003` ProductionLine foundation kısmı kapanır. ISSUE data/UI hâlâ inventory hard gate'leri (`DEC-HG-001`, `DEC-HG-002`, `DEC-HG-005` vb.) ve implementasyon görevleri bekler.

## 3. Açık İş Kararları

Bu tablo legacy kimlikleri silmez. Aynı konuya ait eski kimlikler `Source IDs` alanında kanonik karar kaydına bağlanır.

| Decision ID | Topic | Status | Source IDs | Required Before | Owner/Input Needed | Notes |
|---|---|---|---|---|---|---|
| `DEC-HG-001` | Physical count stock-stability strategy | `DEFERRED_WITH_HARD_GATE` | OD-011, OD-012, DM-B14, UF-O-06, Gate0-AUD-001 | Herhangi bir count/reconciliation schema/service/UI implementasyonu | İş sahibi + operasyon + mimari review | Scoped freeze, as-of snapshot/replay veya kanıtlanmış revalidation/reconfirmation seçeneklerinden biri seçilmeli. Explicit scope, expected timing, idempotent reconciliation, double-apply guard ve serialized discrepancy çözümü zorunlu. |
| `DEC-HG-002` | Correction bounds ve lineage | `DEFERRED_WITH_HARD_GATE` | OD-017, COR-011, DM-B13, UF-O-13, Gate0-AUD-006 | Correction schema/service implementation | İş sahibi + inventory architect | Tek/cumulative approval, partial correction, original-line link, over-correction, later movement, correction-of-correction, requester=approver ve yetersiz current stock cevaplanmalı. |
| `DEC-HG-003` | Production line veri modeli | `DECIDED` | OD-007, DM-B07, UF-O-01, Gate0-AUD-010 | ProductionLine foundation implementation | — | `DEC-025` ile kapatıldı. `ProductionLine` dynamic master-data entity; recursive hierarchy; code/name lifecycle; exact usage place ayrı free text. ISSUE henüz implement edilmez. |
| `DEC-HG-004` | Employee identity linkage ve number reuse | `DECIDED` | OD-026 (employee linkage), DM-B01, DM-B08, UF-O-02, UF-O-16, UF-O-17, Gate0-AUD-017 | Employee foundation implementation (Phase 3.2) | — | `DEC-024` ile kapatıldı (foundation). `accounts.Employee` ayrı entity; sicil string/global unique/editable; nullable one-to-one User link SET_NULL; lifecycle active/inactive. Retention pilot öncesi kararları (`DEC-OPEN-013`, `DEC-OPEN-018`) açık kalır. Receiver snapshot korunur. |
| `DEC-HG-005` | RETURN semantics | `DEFERRED_WITH_HARD_GATE` | OD-004, RET-004, DM-B11, UF-O-04, UF-O-12, Gate0-AUD-018 | Return schema/service/UI | İş sahibi | Prior ISSUE zorunluluğu, partial quantity, condition actor, serialized state ve sistemde issue edilmemiş found/wrong-delivery davranışı cevaplanmalı. RETURN aktif UI'da yer alamaz. |
| `DEC-OPEN-001` | Condition'ın available/minimum stock etkisi | `OPEN` | OD-001, OD-002, OD-003, OD-027, DM-B04, UF-O-11 | Issue availability, return, low-stock report | İş sahibi | Condition ile movement type ayrımı değişmez. |
| `DEC-OPEN-002` | Quantity stock için çoklu lokasyondan seçim/dağıtım | `OPEN` | OD-005 | İlgili issue/picking feature | İş sahibi | Çoklu lokasyonda stok tutabilme modeli desteklenir. |
| `DEC-OPEN-003` | Minimum stock aggregation | `OPEN` | OD-006, DM-B05, UF-O-03 | Low-stock/report implementation | İş sahibi | Global, location veya usable-condition semantics uydurulamaz. |
| `DEC-OPEN-004` | Serialized identifier ve current-state vocabulary | `OPEN` | OD-008, DM-B02, DM-B03, UF-O-08 | Serialized receipt/issue | İş sahibi + mevcut etiket/veri örnekleri | Zorunlu identifier ve serial uniqueness scope belirlenmeli. |
| `DEC-OPEN-005` | Storekeeper operational permissions | `OPEN` | OD-009, AUTH-011 (operational scope), UF-O-09 | İlgili warehouse feature | İş sahibi | Confirmed receipt/issue yetkileri korunur; diğerleri uydurulmaz. Phase 2 access management `DEC-022` ile kararlıdır. |
| `DEC-OPEN-006` | Correction rejection reason zorunluluğu | `PROPOSED` | OD-010, COR-010, DM-S07, UF-O-19 | Correction reject form | İş sahibi | Onaylanana kadar nullable kalır. |
| `DEC-OPEN-007` | Count tolerance, performer ve reconciliation role | `OPEN` | OD-011, DM-B14, UF-O-06 | Counting implementation | İş sahibi | `DEC-HG-001` stability kararından ayrıdır, ikisi de gerekir. |
| `DEC-OPEN-008` | Baseline/cutover approval ve count-session cardinality | `OPEN` | OD-012, DM-B14, DM-B15, UF-O-07 | Baseline schema/cutover implementation | İş sahibi + architecture review | Onay yetkisi ve bir baseline'ın bir mi birden çok count session'a mı bağlanacağı belirlenmeli; yeni executive role uydurulamaz. |
| `DEC-OPEN-009` | Transfer permissions ve zorunlu senaryolar | `OPEN` | OD-013, DM-B12, UF-O-05 | Transfer implementation | İş sahibi | Atomik source/target semantiği `DEC-003` ile şimdiden sabittir. |
| `DEC-OPEN-010` | Unit decimal precision ve unit conversion | `OPEN` | OD-014, DM-B06, UF-O-18 | Unit-specific validation | İş sahibi | Technical default `NUMERIC(18,3)`; conversion yok. |
| `DEC-OPEN-011` | Exceptional tracking-mode migration policy | `OPEN` | OD-015, DM-B10, UF-O-14 | Böyle bir dönüşüm talep edilirse | İş sahibi + migration review | Normal edit yasağı `DEC-013` ile kararlıdır. |
| `DEC-OPEN-012` | Stocked location deactivation | `DECIDED` | OD-016, DM-B09, UF-O-15 | Inventory integration (authoritative stock-row enforcement) | — | `DEC-023` ile kapatıldı. Non-zero stock Location pasifleştirilemez ve `can_hold_stock` True→False yapılamaz; stok önce sıfırlanmalıdır. Phase 3.1 CRUD, stok satırı yokken güvenle uygulanabilir. Inventory aynı invariant'ı otoritatif uygulamak zorundadır. |
| `DEC-OPEN-013` | Photo format/size/currentness/retention | `OPEN` | OD-018, COR-013, DM-P01 | Attachment feature; retention pilot öncesi | Security + iş sahibi | Güvenlik sınırları `DEC-017`, storage/backup sırası `DEC-018` ile kararlıdır. |
| `DEC-OPEN-014` | Report periods, usage/decrease definitions | `OPEN` | OD-019, REP-007, UF-O-10 | Reporting implementation | İş sahibi | Europe/Istanbul presentation default'u korunur. |
| `DEC-OPEN-015` | Excel workbook mapping ve cleansing | `OPEN` | OD-020, IMP-006 | Import implementation | Gerçek workbook + iş sahibi | Candidate stock yasağı `DEC-001` ile kararlıdır. |
| `DEC-OPEN-016` | QR payload, labels ve device rules | `OPEN` | OD-021, OD-029, DM-P02, UF-O-21 | QR feature | Operasyon + IT | Module ownership `DEC-011` ile kararlıdır. |
| `DEC-OPEN-017` | Report timezone/week boundary | `OPEN` | OD-023, TIME-005, UF-O-10 | Reporting implementation | İş sahibi | Storage timezone-aware'dır. |
| `DEC-OPEN-018` | Ledger/audit/photo/import retention periods | `OPEN` | OD-024, TIME-006, DM-P01, UF-O-24 | Pilot/go-live policy | İş sahibi + legal/privacy + IT | Karar çıkana kadar destructive deletion yok. |
| `DEC-OPEN-019` | Category-specific technical attribute schema | `OPEN` | OD-025, MAT-004, Ürün TBD-001/TBD-002 | Catalog/import mapping | Gerçek material/workbook examples | JSONB teknik yönü korunur. |
| `DEC-OPEN-020` | Quantitative stock accuracy target | `OPEN` | OD-028, DM-P04, Ürün TBD-021 | Pilot acceptance calibration | İş sahibi | Fiziksel bulunabilirlik hedefini zayıflatmaz. |
| `DEC-OPEN-021` | Material code uniqueness and format | `OPEN` | DM-B01, UF-O-17 | Catalog/import matching | İş sahibi + existing data | Location code policy `DEC-023` ile kararlıdır ve bu kayıttan ayrılmıştır. Employee number policy `DEC-024` ile kararlıdır ve ayrılmıştır. Material code uniqueness/format ve import matching açık kalır. |
| `DEC-OPEN-022` | Technical attribute advanced search | `OPEN` | UF-O-20 | Advanced search feature | Kullanıcı ihtiyaçları + ölçülmüş sorgular | JSONB GIN/trigram ihtiyaç doğrulanmadan eklenmez. |
| `DEC-OPEN-023` | Offline/mobile retry UX ve sync semantics | `OPEN` | Ürün TBD-029, UF-O-22 | V1 sonrası offline/mobile feature | İş sahibi + mimari review | V1 online web-first; `operation_id`/fingerprint hazırlığı offline sync implementasyonu değildir. |
| `DEC-OPEN-024` | Login/export/security audit detail | `OPEN` | UF-O-23 | Security reporting feature | Security + iş sahibi | Inventory ledger gereksiz generic audit olarak kopyalanmaz. |
| `DEC-OPEN-025` | Minimum-stock notification channel/visibility | `OPEN` | Ürün TBD-018, UF-O-25, DM-P05 | Notification feature | İş sahibi | Low-stock projection notification olmadan kurulabilir. |
| `DEC-OPEN-026` | Performance ve operational service levels | `OPEN` | Ürün TBD-030 | Pilot capacity/performance acceptance | İş sahibi + IT | Ölçüm olmadan cache/worker/analytics altyapısı eklenmez. |
| `DEC-OPEN-027` | V1 dışı broader photo scope | `OPEN` | Ürün TBD-014 | Material/location photo feature talep edilirse | İş sahibi | Current V1 attachment ownership yalnız correction evidence içindir. |

### IT / Deployment Dependencies

| Decision ID | Topic | Status | Source IDs | Required Before | Owner/Input Needed | Notes |
|---|---|---|---|---|---|---|
| `DEC-IT-001` | Production server OS, CPU/RAM/disk | `IT_DEPENDENCY` | Ürün TBD-022/TBD-023 | Deployment | IT | Core business rules değişmez. |
| `DEC-IT-002` | Docker / Docker Compose permission | `IT_DEPENDENCY` | Ürün TBD-024 | Deployment packaging | IT | Yasaksa native service packaging; SQLite fallback yok. |
| `DEC-IT-003` | PostgreSQL hosting/permission | `IT_DEPENDENCY` | Ürün TBD-025 | Production deployment | IT | PostgreSQL semantics mimari karardır; barındırma modeli IT'ye bağlıdır. |
| `DEC-IT-004` | Internal DNS/HTTPS/certificate | `IT_DEPENDENCY` | Ürün TBD-026 | Production security | IT | Secure production config buna göre uygulanır. |
| `DEC-IT-005` | Backup target, schedule, retention, encryption | `IT_DEPENDENCY` | Ürün TBD-027 | Pilot/go-live | IT + system owner | `DEC-018` consistency ve restore drill sözleşmesi zorunludur. |
| `DEC-IT-006` | Mobile/tablet intranet access | `IT_DEPENDENCY` | Ürün TBD-028 | QR/mobile browser pilot | IT | Core web usage bunu beklemez. |
| `DEC-IT-007` | AD/LDAP/SSO requirement | `IT_DEPENDENCY` | AUTH/TBD references | Identity integration | IT | Django Auth default yönüdür. |

## 4. Phase Bazlı Hard Gates

| Before | Mandatory decisions / gates |
|---|---|
| First Django migration / Django bootstrap | `DEC-019`: project-owned `AUTH_USER_MODEL` (`accounts.User`, minimal `AbstractUser`) |
| Employee foundation implementation (Phase 3.2) | `DEC-024`; Employee şekli, sicil, User link, lifecycle, permission ve audit politikası kararlıdır. Employee henüz implement edilmemiştir. |
| ProductionLine foundation implementation | `DEC-025`; şekil, hiyerarşi, code/name, lifecycle ve permission politikası kararlıdır. ProductionLine henüz implement edilmemiştir. |
| Catalog/import identity matching | `DEC-OPEN-004`, `DEC-OPEN-021` (Material code; Location code `DEC-023`; Employee number `DEC-024` ile kararlı); history sonrası tracking mode için `DEC-013` zaten kararlı |
| Issue data/UI | `DEC-HG-001`, `DEC-HG-002`, `DEC-HG-005` ve inventory implementasyonu; `DEC-HG-003`/`DEC-HG-004` foundation kararlı (`DEC-024`, `DEC-025`); receiver snapshots değişmez |
| Technician field intake request/approval workflow | `DEC-020` kararlıdır; talep/onay schema/service/UI implementasyonu ayrı görevdir; hareket türü eşlemesi `DEC-HG-005` çözülmeden yapılmaz |
| Return | `DEC-HG-005`; cevaplanmadan schema/service/UI ve aktif menü yok |
| Corrections | `DEC-HG-002`; ayrıca `DEC-OPEN-006` yalnız PROPOSED kalır |
| Counting/reconciliation | `DEC-HG-001` ve `DEC-OPEN-007`; stability modeli olmadan Phase 11/count implementation başlayamaz |
| Baseline schema/cutover | `DEC-OPEN-008` approval + count-session cardinality; `DEC-002` ve `DEC-015` teknik contract'ları sabittir |
| Low stock/reporting | `DEC-OPEN-003`, `DEC-OPEN-014`, `DEC-OPEN-017` |
| QR | `DEC-OPEN-016`, `DEC-IT-006`; neutral ownership `DEC-011` ile sabittir |
| Deployment/pilot | `DEC-IT-001`–`DEC-IT-005`, restore drill; retention için `DEC-OPEN-013`/`018` |
| Phase 2 catalog/configuration UI | `DEC-021`: dynamic configuration principle, seed≠whitelist, UoM/role/technical-spec boundaries |
| Phase 2.9B-0 access management policy | `DEC-022`: management capability, allowlist, anti-escalation, audit identity, Admin/UI boundary |
| Phase 2 dynamic role management | `DEC-021`, `DEC-022`: yalnız güvenli catalog/configuration permission'ları; inventory receipt/issue/approval izinleri expose edilmez |
| Phase 2.5C `setup_roles` hardening | `DEC-021`: non-destructive bootstrap; mevcut rol permission reconcile yapmaz |
| Phase 2.6 UnitOfMeasure UI | `DEC-021` UoM policy; `DEC-OPEN-010` rounding/conversion çözülmeden precision semantics uydurulmaz |
| Phase 2.8B technical specifications | **Disposition: `DEFER`** (2026-09-11). `DEC-021`, `DEC-OPEN-019` (`OPEN` kalır; yeni DEC yok). Bkz. §4.1. |
| Phase 2.9C technical-field configuration | **Disposition: `SKIPPED`** (2026-09-11). Onaylı gerçek fabrika teknik alan kanıtı yok; Phase 2.8B DEFER otoritatif kalır. `DEC-OPEN-019` (`OPEN` kalır; yeni DEC yok). Bkz. §4.2. |
| Location / ProductionLine UI | Location foundation `DEC-023` (Phase 3.0 COMPLETE). Phase 3.1 Location implementation COMPLETE. `ProductionLine` foundation `DEC-025` (Phase 3.2-0 COMPLETE); implementasyon Employee sonrası. |
| Phase 3.0 Location foundation decisions | **COMPLETE** (2026-09-11). `DEC-023`. Bkz. §4.1 Phase 3.0. |
| Phase 3.1 Location foundation implementation | **COMPLETE**. `DEC-023`. Bkz. §4.1. |
| Phase 3.2-0 Employee + ProductionLine decision pack | **COMPLETE** (2026-09-11). `DEC-024`, `DEC-025`. Bkz. §4.1 Phase 3.2-0. Employee ve ProductionLine henüz implement edilmemiştir. |
| Phase 3.2 Employee foundation implementation | `DEC-024`; sicil, User link, lifecycle, izin ve audit politikası kararlıdır. Employee henüz implement edilmemiştir. Inventory başlamamıştır. |
| Gate 1 | Phase 1.1–1.8 foundation — **Disposition: `PASS`** (tarihsel kayıt/backfill 2026-09-11). Bkz. §4.3. |
| Gate 2 | Phase 2.10 — **Disposition: `PASS`** (2026-09-11). Bkz. §4.4. Phase 2 kapatıldı; Phase 3 başlayabilir. Inventory implementasyonu Phase 2 dışındadır. |

### 4.1 Phase Gate Dispositions

#### Phase 2.8B — Technical Specification Write Gate

- **Disposition:** `DEFER` (2026-09-11)
- **Reason:** `TechnicalFieldDefinition` semantiğini güvenle dondurmak için henüz yeterli gerçek fabrika kanıtı yok. Motor güç/rpm/voltage veya kablo kesit/çekirdek sayısı gibi mevcut örnekler yalnızca illüstratiftir; onaylı kanonik alan tanımı değildir. Şema ve validation semantiği dondurulmadan önce gerçek Excel/malzeme/form örnekleri gerekir.
- **`DEC-OPEN-019`:** `OPEN` kalır; yeni DEC oluşturulmaz.
- **Gate yeniden açılana kadar zorunlu davranış:** `Material.technical_specs` read-only kalır; unrestricted raw JSON editor yok; Material create/update service `technical_specs` kabul etmez; mevcut `technical_specs` değerleri Material update'lerinde korunur; Material detail güvenli generic read-only render kullanabilir; `TechnicalFieldDefinition` modeli, migration, technical-field configuration UI yok.
- **Phase 2.9C:** **Disposition: `SKIPPED`** (2026-09-11). Bkz. §4.2.
- **Yeniden açma girdileri (örnekler):** mevcut Excel kolon/sheet'leri; gerçek Motor, Kablo, Electrical/Switchgear, Automation, X-Ray, ShapeMeter kayıtları; zorunlu/opsiyonel beklentiler; alan veri tipleri; controlled choice'lar; birimler; kategori kalıtım beklentileri.

#### Phase 2.9C — Technical Field Configuration

- **Disposition:** `SKIPPED` (2026-09-11)
- **Reason:** Onaylı gerçek fabrika teknik alan kanıtı şu an mevcut değil. Daha önce onaylanmış Phase 2.8B DEFER gate'i otoritatif kalır.
- **`DEC-OPEN-019`:** `OPEN` kalır; yeni DEC oluşturulmaz.
- **Zorunlu davranış:** `TechnicalFieldDefinition` modeli, migration, technical-field configuration UI ve technical-field kodu oluşturulmaz; Motor/Kablo vb. alan tanımları uydurulmaz. Phase 2.8B §4.1 read-only `technical_specs` sınırı korunur.
- **Sonraki adım:** Phase 2.10 Gate 2 tamamlandı (§4.4). Kanıt sonradan gelirse technical-field configuration ayrı catalog-enhancement fazı olarak yeniden değerlendirilir.

#### Phase 1 — Gate 1

- **Disposition:** `PASS`
- **Recorded/backfilled on:** 2026-09-11 (orijinal audit tarihi repository'de korunmamıştır)
- **Anlam:** Phase 1.1–1.8 foundation kabul edildi ve Phase 2'nin başlamasına izin verildi.
- **Kanıt temeli:** Phase 1.1–1.8 tamamlandı; son Phase 1 foundation/health endpoint işi commit edildi; önceki Gate 1 audit sonucu `PASS`; ardından Phase 2 çalışmaları bu kabul edilmiş foundation üzerinden ilerledi.
- **Not:** Bu kayıt yeni bir audit çalıştırması değildir; eksik kalan tarihsel gate disposition'ının backfill'idir.

#### Phase 2 — Gate 2

- **Disposition:** `PASS`
- **Recorded on:** 2026-09-11
- **Audit conclusion:** Phase 2 Gate 2 passed; Phase 2 may be closed.
- **Anlam:**
  - Phase 2 catalog/configuration foundation kabul edildi
  - Phase 2 kapatılabilir
  - Phase 3 çalışması başlayabilir
  - Phase 2.9C `SKIPPED` kalır (§4.2)
  - Phase 2.8B `DEFER` kalır (§4.1)
  - `DEC-OPEN-019` `OPEN` kalır
- **Kanıt özeti (2026-09-11):** PostgreSQL/model/migration consistency PASS; Category/UoM/Material PASS; immutable audit PASS; `DEC-022` access management PASS; inactive-user security fix PASS; Django Admin bypass closure PASS; targeted suite PASS; full suite 478 passed; repository clean; blocking finding yok.
- **Not:** Gate 2 PASS, `DEC-HG-001`–`DEC-HG-005`, `DEC-OPEN-005`, `DEC-OPEN-010`, `DEC-OPEN-011`, `DEC-OPEN-019` ve `DEC-OPEN-021` (Material code remainder) dahil gelecek faz hard gate/open kararlarını çözmez. Location foundation `DEC-023` ile Phase 3.0'da ayrıca kararlaştırılmıştır.

#### Phase 3.0 — Location Foundation Decisions

- **Disposition:** `COMPLETE` (2026-09-11)
- **Anlam:**
  - Phase 3 master-data principle ve Location şekli/kod/hiyerarşi/`can_hold_stock`/lifecycle/permission/audit/seed politikası `DEC-023` ile kararlıdır
  - `location_type` Phase 3.1 için zorunlu değildir
  - `DEC-OPEN-012` kapatıldı
  - `DEC-OPEN-021` Location code portion'ı ayrılıp kapatıldı; Material code remainder `OPEN` kalır
  - Inventory başlamamıştır
- **Sıradaki (tarihsel):** Phase 3.1 — Location foundation implementation (**COMPLETE**)
- **Açık kalan (tarihsel):** `DEC-HG-001`, `DEC-HG-002`, `DEC-HG-003`, `DEC-HG-004`, `DEC-HG-005`, `DEC-OPEN-019` (son ikisi Phase 3.2-0'da kapatıldı)

#### Phase 3.2-0 — Employee + ProductionLine Decision Pack

- **Disposition:** `COMPLETE` (2026-09-11)
- **Anlam:**
  - Phase 3 master-data principle Employee ve ProductionLine foundation kararlarıyla genişletildi
  - `DEC-HG-003` ProductionLine foundation kısmı `DEC-025` ile kapatıldı
  - `DEC-HG-004` Employee foundation kısmı `DEC-024` ile kapatıldı
  - Employee number policy `DEC-024`; Material code remainder `DEC-OPEN-021`'de açık kalır
  - Employee ve ProductionLine henüz implement edilmemiştir
  - Inventory başlamamıştır
- **Sıradaki:** Phase 3.2 — Employee foundation implementation
- **Sonra:** ProductionLine foundation implementation
- **Açık kalan hard gate'ler:** `DEC-HG-001`, `DEC-HG-002`, `DEC-HG-005`

## 5. Audit Finding Disposition

| Audit ID | Severity | Disposition | Problem | Audit önerisi | Final resolution / gate | Document updated |
|---|---|---|---|---|---|---|
| `AUD-001` | CRITICAL | `DEFER_WITH_HARD_GATE` | Açık count session sırasında hareketlerin expected state'i stale yapması | Freeze, as-of replay veya revalidation seçeneklerinden birini seç | `DEC-HG-001`; bugün seçenek seçilmedi, counting öncesi zorunlu | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-002` | CRITICAL | `ACCEPT_NOW` | Candidate inventory'nin modellenmemesi double stock riski yaratıyor | Import master-only, quantity staging, stock yalnız baseline | `DEC-001`, `DEC-002` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-003` | HIGH | `ACCEPT_NOW` | Her hierarchy node stok tutabiliyor | Stock-eligible capability ekle | `DEC-004`; leaf-only değil `can_hold_stock` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-004` | HIGH | `ACCEPT_NOW` | Ledger immutability yalnız policy | Trigger veya restricted DB role | `DEC-005`; preferred PostgreSQL trigger guard | `02`, `03`, `05`, `AGENTS` |
| `AUD-005` | HIGH | `ACCEPT_NOW` | Nonexistent balance row lock edilemiyor; lock order eksik | Unique + on-conflict + lock ve deterministic ordering | `DEC-006`–`DEC-008` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-006` | HIGH | `DEFER_WITH_HARD_GATE` | Over/partial correction ve lineage belirsiz | Bounds ve line linkage kararları | `DEC-HG-002`; correction implementation öncesi | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-007` | HIGH | `ACCEPT_NOW` | operation_id conflicting payload'u ayıramıyor | Payload fingerprint | `DEC-009`; server-generated SHA-256 canonical fingerprint | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-008` | HIGH | `ACCEPT_NOW` | Projection rebuild/verify yalnız teorik | Verify ve protected rebuild operation | `DEC-014` | `02`, `03`, `05`, `AGENTS` |
| `AUD-009` | HIGH | `ACCEPT_NOW` | Reverse workflow FKs ve barcode ownership dependency cycle yaratıyor | İlişkileri downstream'e taşı; neutral identification | `DEC-010`, `DEC-011` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-010` | MEDIUM | `ACCEPT_NOW` | Free-text production line reporting kalitesini bozar | Controlled reference list | `DEC-025`; `ProductionLine` dynamic master-data entity; exact usage place ayrı free text; ISSUE henüz implement edilmez | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-011` | MEDIUM | `ACCEPT_WITH_DIFFERENT_SOLUTION` | Quantity/serialized cross-table corruption için DB backstop yok | Redundant mode kolonları + composite FK | `DEC-012`, `DEC-013`; service + targeted DB guards, redundant mode kolonları varsayılan değil | `02`, `03`, `05`, `AGENTS` |
| `AUD-012` | MEDIUM | `ACCEPT_NOW` | Admin bypass yüzeyi eksik tanımlı | Explicit read-only/no-actions/no-inline policy | `DEC-016` | `05`, `AGENTS` |
| `AUD-013` | MEDIUM | `ACCEPT_NOW` | Attachment IDOR, parser exhaustion ve formula injection riskleri | Permission view, bounded parser, export neutralization | `DEC-017` | `04`, `05`, `AGENTS` |
| `AUD-014` | MEDIUM | `ACCEPT_NOW` | Direction inference normatif değil | Source decreases, target increases kuralı | `DEC-003` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-015` | MEDIUM | `ACCEPT_WITH_DIFFERENT_SOLUTION` | DB/media consistency ve restore verification eksik | Media-first backup ve cross-check | `DEC-018`; file-before-DB invariant'ı altında DB-first then media, deletion sonrası coordinated snapshot | `05`, `AGENTS` |
| `AUD-016` | MEDIUM | `ACCEPT_NOW` | Tek dev INITIAL_BALANCE transaction cutover riski | Scoped multiple transactions | `DEC-015` | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-017` | LOW | `ACCEPT_WITH_DIFFERENT_SOLUTION` | Employee number reuse ve retention belirsiz | Uniqueness/reuse ve privacy kararları | `DEC-024`; foundation identity/link kararlı; retention pilot öncesi `DEC-OPEN-013`/`018` açık; eski sicil ayrı registry ile rezerve edilmez, snapshot korunur | `02`, `03`, `04`, `05`, `AGENTS` |
| `AUD-018` | LOW | `DEFER_WITH_HARD_GATE` | RETURN enum'da var fakat semantiği belirsiz | Beş business decision | `DEC-HG-005`; karar olmadan implementation/UI yok | `02`, `03`, `04`, `05`, `AGENTS` |

Disposition toplamı:

- `ACCEPT_NOW`: **12**
- `ACCEPT_WITH_DIFFERENT_SOLUTION`: **3**
- `DEFER_WITH_HARD_GATE`: **3**
- `REJECT_WITH_REASON`: **0**
- Toplam: **18**

## 6. Legacy ID Index

Legacy kimlikler silinmez; toplu eşlemeler aşağıdaki kanonik kayıtlara yönelir:

| Legacy IDs | Canonical decision |
|---|---|
| `OD-001`, `OD-002`, `OD-003`, `OD-027`, `DM-B04`, `UF-O-11` | `DEC-OPEN-001` |
| `OD-004`, `DM-B11`, `UF-O-04`, `UF-O-12` | `DEC-HG-005` |
| `OD-005` | `DEC-OPEN-002` |
| `OD-006`, `DM-B05`, `UF-O-03` | `DEC-OPEN-003` |
| `OD-007`, `DM-B07`, `UF-O-01` | `DEC-025` (`DEC-HG-003` DECIDED) |
| `OD-008`, `DM-B02`, `DM-B03`, `UF-O-08` | `DEC-OPEN-004` |
| `OD-009`, `UF-O-09` | `DEC-OPEN-005` |
| `OD-010`, `UF-O-19` | `DEC-OPEN-006` |
| `OD-011`, `DM-B14`, `UF-O-06` | `DEC-HG-001`, `DEC-OPEN-007` |
| `OD-012`, `DM-B15`, `UF-O-07` | `DEC-OPEN-008` |
| `OD-013`, `DM-B12`, `UF-O-05` | `DEC-OPEN-009` |
| `OD-014`, `DM-B06`, `UF-O-18` | `DEC-OPEN-010` |
| `OD-015`, `DM-B10`, `UF-O-14` | `DEC-013`, `DEC-OPEN-011` |
| `OD-016`, `DM-B09`, `UF-O-15` | `DEC-004`, `DEC-023` (`DEC-OPEN-012` DECIDED) |
| `OD-017`, `DM-B13`, `UF-O-13` | `DEC-HG-002` |
| `OD-018`, `DM-P01` | `DEC-OPEN-013` |
| `OD-019`, `UF-O-10` | `DEC-OPEN-014` |
| `OD-020` | `DEC-OPEN-015` |
| `OD-021`, `OD-029`, `DM-P02`, `UF-O-21` | `DEC-OPEN-016` |
| `OD-022`, `DM-B16` | Closed by `DEC-006`–`DEC-009` |
| `OD-023`, `UF-O-10` | `DEC-OPEN-017` |
| `OD-024`, `DM-P01`, `UF-O-24` | `DEC-OPEN-018` |
| `OD-025` | `DEC-OPEN-019` |
| `OD-026` (rol atama/onay) | `DEC-022` |
| `OD-026` (employee linkage), `DM-B01`, `DM-B08`, `UF-O-02`, `UF-O-16`, `UF-O-17` | `DEC-024` (`DEC-HG-004` DECIDED); Material code remainder `DEC-OPEN-021` |
| `OD-028`, `DM-P04` | `DEC-OPEN-020` |

`ADR-*` kimlikleri `docs/05-ARCHITECTURE.md` içindeki architecture summary kayıtları olarak korunur. `DOC-*` maddeleri dokümantasyon borcu kimlikleridir; iş kararı değildir. `DM-S*` teknik safe default, `DM-P*` pilot deferral kayıtlarıdır ve ilgili kanonik kararlara referans olabilir.

### Product TBD Index

| Product IDs | Canonical decision |
|---|---|
| `TBD-001`, `TBD-002` | `DEC-OPEN-019` |
| `TBD-003` | `DEC-OPEN-004`, `DEC-013`, `DEC-OPEN-011` |
| `TBD-004` | `DEC-OPEN-001`; movement/condition ayrımı zaten onaylıdır |
| `TBD-005` | `DEC-HG-002`, `DEC-HG-005` |
| `TBD-006` | `DEC-OPEN-009` |
| `TBD-007` | `DEC-OPEN-010` |
| `TBD-008` | `DEC-023` (hiyerarşi/kod/lifecycle); çoklu konum `DEC-OPEN-002`; sayım alanı `DEC-HG-001` |
| `TBD-009` | `DEC-025` |
| `TBD-010` | `DEC-024` |
| `TBD-011` | `DEC-OPEN-005`, `DEC-OPEN-007`, `DEC-OPEN-008`, `DEC-OPEN-009` |
| `TBD-012` | `DEC-HG-002`, `DEC-OPEN-006` |
| `TBD-013` | `DEC-OPEN-013`, `DEC-OPEN-018` |
| `TBD-014` | `DEC-OPEN-027` |
| `TBD-015`, `TBD-016` | `DEC-OPEN-016` |
| `TBD-017` | `DEC-OPEN-014`, `DEC-OPEN-017`, `DEC-OPEN-024` |
| `TBD-018` | `DEC-OPEN-025` |
| `TBD-019` | `DEC-OPEN-015` |
| `TBD-020` | `DEC-HG-001`, `DEC-OPEN-007`, `DEC-OPEN-008` |
| `TBD-021` | `DEC-OPEN-020` |
| `TBD-022`, `TBD-023` | `DEC-IT-001` |
| `TBD-024` | `DEC-IT-002` |
| `TBD-025` | `DEC-IT-003` |
| `TBD-026` | `DEC-IT-004` |
| `TBD-027` | `DEC-IT-005` |
| `TBD-028` | `DEC-IT-006` |
| `TBD-029` | `DEC-OPEN-023` |
| `TBD-030` | `DEC-OPEN-026` |

## 7. Gate 0 Sonrası Kural

Task 0.9 tamamlandıktan sonra Phase 1 otomatik başlamaz. Bu belge, diğer yetkili dokümanlar ve `AGENTS.md` bağımsız Gate 0 re-audit'e girer. Re-audit; 18 finding disposition'ının gerçekten tüm ilgili belgelerde karşılığını, hard gate'lerin açık iş kararı olarak kaldığını ve 12 zorunlu correctness senaryosunun doküman tasarımıyla karşılandığını doğrulamalıdır.
