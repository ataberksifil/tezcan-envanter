# İş Kuralları — Elektrik Atölyesi Envanter ve Malzeme Takip Sistemi

## 1. Belge Amacı

Bu belge, `docs/00-PRODUCT.md` içindeki onaylanmış ürün gereksinimlerini doğrulanabilir iş kurallarına dönüştürür. Kurallar daha sonra veri kısıtları, servis davranışları, yetkilendirme, doğrulama, otomatik testler, kabul testleri ve denetim gereksinimleri için kaynak olarak kullanılacaktır.

Bu belge teknik mimari, veri modeli veya uygulama teknolojisi seçmez. Ürün belgesinde açık bırakılan bir konu burada varsayımla sonuçlandırılmaz.

## 2. Kural Sınıflandırması

- **CONFIRMED:** Yetkili ürün belgesine veya Task 0.2 girdisine dayanan, uygulanması zorunlu kural.
- **PROPOSED:** İş bütünlüğü için önerilen fakat henüz iş birimi tarafından onaylanmamış kural.
- **TBD DEPENDENCY:** Kuralın onaylı kısmı bilinir; tamamlanması için açık bir iş veya teknik karar gerekir.

Kural kimlikleri kalıcı referans olarak kullanılmalıdır. Bir kural değişirse mümkün olduğunca kimliği korunmalı, anlamlı değişiklik geçmişi izlenebilir olmalıdır.

## 3. Genel Envanter Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| INV-001 | CONFIRMED | Stok miktarı, kaydedilmiş bir iş olayı veya eşdeğer denetlenebilir kayıt olmadan değişemez. | Her miktar farkı kaynak olaya kadar izlenebilmelidir. |
| INV-002 | CONFIRMED | Onaylanmış stok iş olayları giriş, çıkış, iade, uygulanabildiği yerde transfer ve kontrollü düzeltmedir. | Bunların dışında zorunlu bir hareket türü bu aşamada varsayılmaz. |
| INV-003 | CONFIRMED | Tamamlanmış stok işlemi normal operasyon kullanıcıları tarafından sessizce düzenlenemez veya silinemez. | Eski ve yeni değerlerin yalnızca son hâlini gösteren bir değişiklik kabul edilmez. |
| INV-004 | CONFIRMED | Hatalı tamamlanmış işlem, kontrollü düzeltme iş akışı üzerinden ele alınmalıdır. | Doğrudan geçmiş değiştirme girişimi reddedilmelidir. |
| INV-005 | CONFIRMED | Hiçbir stok çıkışı, transfer, düzeltme veya başka envanter işlemi kullanılabilir stok miktarını sıfırın altına düşüremez. | Negatif sonuç üreten işlem tamamlanmamalıdır. |
| INV-006 | CONFIRMED | Negatif stok kontrolü, stok değişikliği kesinleşmeden önce geçerli malzeme ve konum kapsamındaki güncel kullanılabilir stok üzerinden yapılmalıdır. | Sınır değerdeki bir çıkış sıfır bırakabilir; sıfır altına inen çıkış reddedilir. |
| INV-007 | CONFIRMED | Güncel stok yetkili kullanıcıya malzeme ve fiziksel konum bağlamında gösterilebilmelidir. | Aynı malzemenin farklı konumlardaki stokları ayırt edilebilmelidir. |
| INV-008 | TBD DEPENDENCY | Eş zamanlı işlemler de negatif stok oluşturamamalıdır; bunu garanti edecek atomiklik ve eşzamanlılık yöntemi Faz 0.3 ve mimari çalışmada belirlenecektir. | Aynı stoku kullanan iki eş zamanlı işlemin toplam sonucu negatif olamamalıdır. |

## 4. Malzeme ve Takip Modu Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| MAT-001 | CONFIRMED | Her stok kaydı geçerli bir malzemeye bağlı olmalıdır. | Tanımsız malzeme için stok hareketi tamamlanamaz. |
| MAT-002 | CONFIRMED | Malzemeler kategori ve gelecekte genişletilebilir alt kategoriler altında sınıflandırılabilmelidir. | Başlangıç üst kategorileri korunurken alt kategori eklenebilmelidir. |
| MAT-003 | CONFIRMED | Malzeme kataloğu, kategoriye göre farklılaşabilen esnek teknik nitelikleri desteklemelidir. | Ayrıntılı alan listesi olmadan da farklı kategori şemalarının desteklenebilirliği korunmalıdır. |
| MAT-004 | TBD DEPENDENCY | Kategoriye özgü teknik nitelikler gerçek malzeme örnekleri ve Excel analizi sonrası kesinleştirilecektir. | Henüz hiçbir teknik alan zorunlu kabul edilemez. |
| MAT-005 | CONFIRMED | Takip modu kategori tarafından değişmez biçimde belirlenemez; her malzeme için yapılandırılabilir olmalıdır. | Aynı kategoride farklı takip ihtiyaçları desteklenebilmelidir. |
| QTY-001 | CONFIRMED | Sistem miktar bazlı stok takibini desteklemelidir. | Miktar bazlı bir malzemenin bakiyesi hareketlerden hesaplanabilir veya doğrulanabilir olmalıdır. |
| QTY-002 | CONFIRMED | Miktar, malzeme için geçerli ölçü birimine uygun olarak kaydedilmelidir. | Birimsiz veya malzemenin birimiyle uyumsuz miktar kabul edilmemelidir. |
| QTY-003 | CONFIRMED | Gelecekteki sistem en az adet, metre, makara, set ve paket gibi birimleri destekleyebilecek yapıda olmalıdır. | Bu liste birimler arası dönüşüm tanımlamaz. |
| QTY-004 | CONFIRMED | Miktar bazlı bir malzeme bir veya birden fazla geçerli fiziksel depolama konumunda bulunabilmelidir. | Aynı malzemenin konum bakiyeleri birbirinden ayırt edilebilmelidir. |
| QTY-005 | TBD DEPENDENCY | Birim dönüşümü, ondalık hassasiyet, kısmi miktar ve çoklu konuma dağıtım kuralları henüz belirlenmemiştir. | Bu kararlar verilmeden otomatik birim dönüşümü yapılamaz. |
| SER-001 | CONFIRMED | Sistem tekil olarak izlenen fiziksel varlıkları desteklemelidir. | Tekil takip modundaki varlık miktar bakiyesiyle kimliksiz biçimde birleştirilemez. |
| SER-002 | CONFIRMED | Her tekil fiziksel varlık sistem içinde benzersiz bir kimliğe sahip olmalıdır. | Aynı sistem kimliği iki fiziksel varlığı temsil edemez. |
| SER-003 | CONFIRMED | Tek bir fiziksel tekil varlık aynı anda iki farklı fiziksel konumda mevcut gösterilemez. | İkinci konuma yerleştirme, önceki konumla tutarlı izlenebilir bir hareket gerektirir. |
| SER-004 | TBD DEPENDENCY | Tekil varlık için seri numarası, iç varlık kodu veya başka hangi tanımlayıcıların zorunlu olacağı belirlenmemiştir. | Zorunlu tanımlayıcılar Faz 0.3 öncesi veya sırasında iş kararıyla netleşmelidir. |
| SER-005 | TBD DEPENDENCY | Aktif stoku veya hareket geçmişi bulunan bir malzemenin takip modunun değiştirilme koşulları belirlenmemiştir. | Takip modu değişikliği geçmiş kimlik veya miktar bütünlüğünü bozamamalıdır. |

## 5. Kondisyon Kuralları

Bu belgede “kondisyon”, malzemenin fiziksel/operasyonel durumunu ifade eder; stok hareketinin nedenini veya yönünü ifade etmez.

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| COND-001 | CONFIRMED | Hareket türü ile malzeme kondisyonu ayrı iş kavramları olarak korunmalıdır. | “Çıkma” veya “bozuk” ifadesi tek başına giriş/çıkış hareketi yerine kullanılamaz. |
| COND-002 | CONFIRMED | İş terminolojisi en az yeni/sağlam, çıkma-kullanılmış/sağlam, bozuk ve çıkma-kullanılmış/bozuk durumlarını ayırt edebilmelidir. | Kayıt ve raporlama bu iş anlamlarını kaybetmemelidir. |
| COND-003 | CONFIRMED | Tekil varlığın kondisyonu varlığın benzersiz kimliğiyle ilişkilendirilmeli ve raporlanabilir olmalıdır. | Kondisyon değişse bile aynı fiziksel varlığın izlenebilirliği korunmalıdır. |
| COND-004 | CONFIRMED | Miktar bazlı stokta farklı kondisyonlar birbirinden ayırt edilebilmelidir. | Farklı kondisyonlardaki miktarlar raporda tek, ayırt edilemez bakiyeye dönüşmemelidir. |
| COND-005 | TBD DEPENDENCY | Kondisyonun kullanılabilir stok hesabına, minimum stok hesabına, iade uygunluğuna ve hareket akışına kesin etkisi belirlenmemiştir. | “Bozuk” stoğun kullanılabilir sayılıp sayılmayacağı varsayılamaz. |
| COND-006 | TBD DEPENDENCY | “Çıkma sağlam” ve “çıkma bozuk” ifadelerindeki “çıkma”nın yalnızca kondisyon geçmişi mi yoksa ayrıca bir iş olayı mı gerektirdiği kesinleştirilmemiştir. | Terminoloji kararı verilmeden yeni zorunlu hareket türü oluşturulamaz. |

## 6. Lokasyon Kuralları

Bilinen başlangıç alanları Elektrik Deposu, Alkali Elektrik alanındaki kablo stoğu, Enstrüman Atölyesi ve Bobinaj Atölyesi'dir.

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| LOC-001 | CONFIRMED | Fiziksel olarak stokta bulunan her miktar veya tekil varlık geçerli bir fiziksel lokasyonla ilişkilendirilmelidir. | Lokasyonsuz mevcut stok oluşturulamamalıdır. |
| LOC-002 | CONFIRMED | Lokasyon takibi raf seviyesine kadar yapılmalıdır. | Sistem “mevcut” dediğinde malzemenin aranacağı raf belirlenebilmelidir. |
| LOC-003 | CONFIRMED | Lokasyonlar gelecekte genişletilebilir bir hiyerarşi içinde düzenlenebilmelidir. | Yeni alan ve alt seviyeler eklenebilmelidir. |
| LOC-004 | CONFIRMED | Lokasyonlar arası stok değişimi kaynak ve hedef lokasyonlarıyla izlenebilir olmalıdır. | Hareket sonrası stok iki lokasyonda doğru yansımalıdır. |
| LOC-005 | CONFIRMED | Geçmiş işlemde referans verilen lokasyon bilgisi denetim geçmişinden kaybolmamalıdır. | Lokasyon sonradan kullanımdan kalksa bile eski hareket anlaşılabilir kalmalıdır. |
| LOC-006 | CONFIRMED | Kullanımdan kaldırılmış bir lokasyon yeni stok hareketleri için seçilememelidir. | Aktif olmayan lokasyona yeni giriş veya transfer engellenmelidir. |
| LOC-007 | CONFIRMED | Yetkili envanter varken non-zero stock'lu lokasyon pasifleştirilemez ve `can_hold_stock` True→False yapılamaz; stok önce taşınmalı veya mutabakatla sıfırlanmalıdır (`DEC-023`). Hard delete iş operasyonu yoktur. | Mevcut stok sahipsiz bırakılamaz. Inventory entegrasyonu aynı invariant'ı otoritatif uygular; Phase 3.1 CRUD stok satırı yokken güvenle uygulanabilir. |
| LOC-008 | PARTIALLY CONFIRMED | Lokasyon kodu, dinamik hiyerarşi ve `can_hold_stock` `DEC-023` ile kararlıdır. Sayım alanları (`DEC-HG-001`) ve çoklu lokasyon dağıtımı (`DEC-OPEN-002`) açık kalır. | Phase 3.1 Location foundation bu açık konulara bağlı değildir. |

## 7. Stok Giriş Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| RCV-001 | CONFIRMED | Depo Görevlisi ve Yönetici/Müdür stok girişi yapabilir. | Bu rollerdeki yetkili kullanıcı için giriş işlemi erişilebilir olmalıdır. |
| RCV-002 | CONFIRMED | Teknisyen yeni satın alınan veya tedarikçi teslimatı olarak gelen malzemenin olağan stok girişini/kabulünü yapamaz. | Teknisyen rolündeki kullanıcı tedarikçi/satın alma giriş işlemini tamamlayamamalıdır; saha/atölye senaryoları olağan `RECEIPT` değildir ve `AUTH-003A` / `INT-001`–`INT-005` kapsamında yalnızca onaylı talep akışıyla ele alınır (`DEC-020`). |
| RCV-003 | CONFIRMED | Tamamlanan stok girişi denetlenebilir bir stok iş olayı oluşturmalıdır. | Girişin kullanıcı, zaman, malzeme, miktar/varlık ve lokasyon bilgisi izlenebilmelidir. |
| RCV-004 | CONFIRMED | Stok girişi, malzemenin takip moduna uygun miktar veya tekil varlık bilgisiyle ve geçerli hedef lokasyonla kaydedilmelidir. | Miktar ve tekil takip birbirine karıştırılmamalıdır. |
| RCV-005 | TBD DEPENDENCY | Satın alma, sipariş, tedarikçi, irsaliye ve kabul kontrolü kuralları onaylanmamıştır. | Bu bilgiler stok girişi için zorunlu kabul edilemez. |

### 7.1 Saha / Atölye Malzeme Alım Talepleri

Bu alt bölüm, satın alma/tedarikçi `RECEIPT` akışından (`RCV-001`, `RCV-002`) ayrıdır. Depo Görevlisi olağan giriş yetkisi bu kararla değişmez (`DEC-020`).

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| INT-001 | CONFIRMED | Uygun saha veya sahada kullanılan alandan atölyeye fiziksel getirilen malzeme için Teknisyen yalnızca alım talebi başlatabilir; otoritatif stok girişi veya envanter etkisini doğrudan kaydedemez. | Talep, onay öncesi envanter değiştirmez. |
| INT-002 | CONFIRMED | Teknisyen tarafından başlatılan her saha/atölye malzeme alım talebi, otoritatif envanter etkisi öncesinde Yönetici/Müdür onayı gerektirir. | Onay yetkisi `AUTH-012` ile aynıdır. |
| INT-003 | CONFIRMED | Bekleyen talep sırasında `InventoryTransaction` / ledger etkisi, `StockBalance` artışı ve `SerializedAsset` state/lokasyon değişikliği oluşmamalıdır. | Bekleme durumu stok projection'ını değiştiremez. |
| INT-004 | CONFIRMED | Reddedilen talep envanter etkisi oluşturmamalıdır; talep ve kanıt, ilgili workflow ileride uygulandığında tarihsel olarak izlenebilir kalmalıdır. | Red, sessiz stok düzeltmesi değildir. |
| INT-005 | CONFIRMED | Onaylanan talebin envanter etkisi yalnızca gelecekteki otoritatif envanter servisi üzerinden atomik ve idempotent olarak gerçekleşmelidir. | `RETURN`, `RECEIPT`, `TRANSFER`, `CONTROLLED_CORRECTION` veya yeni hareket türüne önceden eşleştirme yapılmaz; `DEC-HG-005` ve ilgili hard gate'ler korunur. |
| INT-006 | CONFIRMED | Daha önce çıkış yapılmış ve tamamen kullanılmamış malzeme, orijinal kondisyonuyla geri gelebilir; ancak uygunluk, miktar limitleri, tekil kimlik, provenans ve kondisyon geçişleri `DEC-HG-005` kapsamında açık kalır. | İş senaryosu örneğidir; RETURN semantiği hard-gated kalır. |

Kavramsal iş senaryoları (hareket türü eşlemesi yapılmaz): tamamen kullanılmamış geri getirilen malzeme; kısmen kullanılmamış geri getirilen malzeme; yanlış alınmış ve kullanılmamış iade; kullanılmış ve sonra sökülmüş malzeme; arızalı/sökülmüş malzeme; orijinal depo `ISSUE` kaydı bilinmeyen fabrika sahası malzemesi.

## 8. Stok Çıkış Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| ISS-001 | CONFIRMED | Teknisyen, Depo Görevlisi ve Yönetici/Müdür stok çıkışı yapabilir. | Diğer roller için ayrıca yetki onayı gerekir. |
| ISS-002 | CONFIRMED | Stok çıkışı teslim alan kişinin adını içermelidir. | Ad eksikse işlem tamamlanmamalıdır. |
| ISS-003 | CONFIRMED | Stok çıkışı teslim alan kişinin soyadını içermelidir. | Soyad eksikse işlem tamamlanmamalıdır. |
| ISS-004 | CONFIRMED | Stok çıkışı teslim alan kişinin çalışan sicil numarasını içermelidir. | Sicil numarası eksikse işlem tamamlanmamalıdır. |
| ISS-005 | CONFIRMED | Stok çıkışı sistem tarafından kaydedilen işlem zamanını içermelidir. | Normal kullanıcı tarafından girilmiş veya sessizce değiştirilmiş zaman asıl işlem zamanı olamaz. |
| ISS-006 | CONFIRMED | Stok çıkışı malzemenin kullanılacağı üretim hattını içermelidir. | Üretim hattı eksikse işlem tamamlanmamalıdır. |
| ISS-007 | CONFIRMED | Stok çıkışı malzemenin fiili kullanım yerini içermelidir. | Fiili kullanım yeri eksikse işlem tamamlanmamalıdır. |
| ISS-008 | CONFIRMED | Çıkış, ilgili malzeme ve kaynak lokasyondaki kullanılabilir stoğu aşamaz. | Aşan miktar veya mevcut olmayan tekil varlık için işlem reddedilmelidir. |
| ISS-009 | CONFIRMED | Tamamlanan çıkış kullanıcı, malzeme/varlık, kaynak lokasyon ve miktar bilgisiyle izlenebilir olmalıdır. | Çıkışın stok üzerindeki etkisi geçmişten doğrulanabilmelidir. |
| ISS-010 | TBD DEPENDENCY | Üretim hattı ile fiili kullanım yerinin sözlükleri, ilişkisi ve teslim alan çalışanın sistem kullanıcısıyla ilişkisi belirlenmemiştir. | Zorunlu alanlar korunur; doğrulama kaynağı Faz 0.3 için TBD'dir. |

## 9. İade Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| RET-001 | CONFIRMED | Sistem iade işlemlerini desteklemelidir. | Yetkili bir iade, denetlenebilir iş olayı olarak kaydedilebilmelidir. |
| RET-002 | CONFIRMED | İade işlemi stok miktarını, lokasyonu veya tekil varlığın durumunu etkiliyorsa bu etki izlenebilir olmalıdır. | İade stok bakiyesini kayıt dışı değiştiremez. |
| RET-003 | CONFIRMED | Her çıkışı yapılmış malzemenin iade edilebilir olduğu varsayılamaz. | Uygunluğu doğrulanmamış iade otomatik kabul edilmemelidir. |
| RET-004 | TBD DEPENDENCY | İade uygunluğu, iade yetkileri, önceki çıkışla bağlantı, hedef lokasyon ve kondisyon etkisi belirlenmemiştir. | Ayrıntılı iade kabul testleri karar sonrasına bırakılır. |

## 10. Transfer Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| TRF-001 | CONFIRMED | Operasyonel olarak gerekli olduğu doğrulanan senaryolarda lokasyonlar arası transfer desteklenmelidir. | Transfer V1 yönüdür; uygulanacağı olaylar ayrıca netleşecektir. |
| TRF-002 | CONFIRMED | Transfer kaynak ve hedef lokasyonlarıyla kaydedilmelidir. | İki lokasyondaki stok etkisi aynı iş olayıyla izlenebilmelidir. |
| TRF-003 | CONFIRMED | Transfer kaynak lokasyonda negatif stok oluşturamaz. | Yetersiz kaynak miktarı veya kaynakta bulunmayan tekil varlık için transfer reddedilmelidir. |
| TRF-004 | CONFIRMED | Tekil varlık transferden sonra aynı anda yalnızca hedef fiziksel lokasyonda mevcut gösterilmelidir. | Kaynak ve hedefte eş zamanlı çift varlık oluşamaz. |
| TRF-005 | TBD DEPENDENCY | Transferi gerektiren iş senaryoları, yetkili roller ve transferin işlem adımları belirlenmemiştir. | Bu kararlar olmadan her lokasyon değişimi için aynı akış varsayılamaz. |

## 11. Düzeltme Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| COR-001 | CONFIRMED | Hatalı tamamlanmış envanter işlemi doğrudan yeniden yazılamaz veya silinemez. | Orijinal işlem denetlenebilir kalmalıdır. |
| COR-002 | CONFIRMED | İşlemi yapan veya hatayı tespit eden yetkili kullanıcı düzeltme talebi oluşturabilmelidir. | Talep hakkı yalnızca ilk işlemi yapan kişiyle sınırlı değildir. |
| COR-003 | CONFIRMED | Düzeltme talebi, talebi oluşturan kullanıcıyı içermelidir. | İstek sahibi kimliği olmadan talep oluşturulamamalıdır. |
| COR-004 | CONFIRMED | Düzeltme talebi açıklama içermelidir. | Boş açıklamayla talep oluşturulamamalıdır. |
| COR-005 | CONFIRMED | Düzeltme talebi güncel destekleyici fotoğraf içermelidir. | Fotoğraf olmadan talep onay sürecine gönderilememelidir. |
| COR-006 | CONFIRMED | Düzeltme talebi Yönetici/Müdür kararı olmadan stok üzerinde onaylanmış düzeltme etkisi oluşturamaz. | Bekleyen talep stok geçmişini kendiliğinden değiştiremez. |
| COR-007 | CONFIRMED | Yönetici/Müdür düzeltme talebini onaylayabilir veya reddedebilir. | Teknisyen ve ayrıca yetkilendirilmemiş Depo Görevlisi karar veremez. |
| COR-008 | CONFIRMED | Düzeltme kararında onaylayan veya reddeden kullanıcı ile karar zamanı kaydedilmelidir. | Kararın sahibi ve zamanı denetlenebilmelidir. |
| COR-009 | CONFIRMED | Düzeltme uygulansa bile orijinal işlem ile düzeltme talebi arasındaki ilişki izlenebilir kalmalıdır. | Denetimde önceki olay, talep ve sonuç birlikte görülebilmelidir. |
| COR-010 | PROPOSED | Reddedilen düzeltme talebi için ret gerekçesi zorunlu olmalıdır. | Ürün belgesi ret gerekçesini zorunlu kılmamaktadır; iş birimi onayı gereklidir. |
| COR-011 | TBD DEPENDENCY | Düzeltmenin ters hareket, dengeleme kaydı veya başka kontrollü yöntemle uygulanacağı belirlenmemiştir. | Teknik/veri modeli çözümü Faz 0.3 ve sonraki mimari görevlerin konusudur. |
| COR-012 | TBD DEPENDENCY | Talep eden ile karar verenin aynı kişi olup olamayacağı; iptal ve yeniden gönderim kuralları belirlenmemiştir. | Görev ayrılığı kuralı varsayılamaz. |
| COR-013 | TBD DEPENDENCY | Fotoğrafın “güncel” sayılma ölçütü, dosya koşulları, erişimi ve saklama süresi belirlenmemiştir. | Yalnızca fotoğraf zorunluluğu onaylıdır. |

## 12. Kullanıcı ve Yetki Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| AUTH-001 | CONFIRMED | Envanter işlevlerine erişim kimliği doğrulanmış kullanıcı ve permission/policy tabanlı yetki üzerinden sağlanmalıdır. `TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER` başlangıç rol şablonlarıdır (`DEC-021`). | Yetkisiz kullanıcı korunan işlemi tamamlayamamalıdır; hard-coded Group adı kontrolü yeterli değildir. |
| AUTH-002 | CONFIRMED | Teknisyen stok ve katalog verisini görüntüleyebilir. | Rol kabul testinde güncel stok ve katalog erişilebilir olmalıdır. |
| AUTH-003 | CONFIRMED | Teknisyen olağan stok çıkışı yapabilir. | Çıkışın diğer zorunlu kuralları yine uygulanır. |
| AUTH-003A | CONFIRMED | Teknisyen satın alma/tedarikçi teslimatı kabulü yapamaz (`RCV-002`). Uygun saha veya sahada kullanılan alandan atölyeye fiziksel getirilen malzeme için yalnızca alım talebi başlatabilir; otoritatif stok girişi veya envanter etkisini doğrudan kaydedemez. | `INT-001`–`INT-005` ve `DEC-020` geçerlidir; hareket türü eşlemesi `DEC-HG-005` çözülmeden yapılmaz. |
| AUTH-012 | CONFIRMED | Yönetici/Müdür, Teknisyen tarafından başlatılan saha/atölye malzeme alım taleplerini onaylayabilir veya reddedebilir. | Onay, otoritatif envanter etkisinin önkoşuludur; red envanter etkisi oluşturmaz (`INT-002`, `INT-004`, `DEC-020`). |
| AUTH-004 | CONFIRMED | Teknisyen uygulanabilir durumda düzeltme talebi oluşturabilir. | Talep oluşturmak, talebi onaylama yetkisi vermez. |
| AUTH-005 | CONFIRMED | Teknisyen envanter ana verisini serbestçe yönetemez ve düzeltme talebi onaylayamaz. | Ana veri değişikliği ve karar işlemi reddedilmelidir. |
| AUTH-006 | CONFIRMED | Depo Görevlisi stok görüntüleyebilir, katalog verisini görüntüleyebilir, olağan giriş ve çıkış yapabilir. | Her işlem kendi doğrulama kurallarına tabidir. |
| AUTH-007 | CONFIRMED | Depo Görevlisi yetkisi kapsamındaki operasyonel depo işlerini yapabilir. | Ayrıntılı işlem listesi TBD'dir. |
| AUTH-008 | CONFIRMED | Depo Görevlisi ayrıca açıkça yetkilendirilmedikçe düzeltme talebi onaylayamaz veya reddedemez. | Mevcut onay yetkisi Yönetici/Müdür rolündedir. |
| AUTH-009 | CONFIRMED | Yönetici/Müdür uygulama içinde tam yönetim yetkisine, ana veri yönetimine ve kontrollü düzeltme yönetimine sahiptir. | Bu rol giriş, çıkış ve düzeltme kararlarını gerçekleştirebilmelidir. |
| AUTH-010 | CONFIRMED | Yönetici/Müdür düzeltme taleplerini onaylayabilir veya reddedebilir. | Karar kullanıcı ve zamanla kaydedilmelidir. |
| AUTH-011 | PARTIALLY CONFIRMED | Phase 2 erişim yönetimi (`DEC-022`): Django Group rol modeli; `accounts.manage_access` delegation capability; dokuz güvenli catalog permission allowlist; write/view invariant; rol lifecycle; yalnız `User.groups` yönetimi; anti-escalation kuralları; ikinci manager onayı yok; audited mutation. Phase 3 Location izni genişlemesi (`DEC-022` item 13, `DEC-023`): `locations.view_location`, `locations.add_location`, `locations.change_location`; `add`/`change` `view` gerektirir; `delete` expose edilmez; `setup_roles` non-destructive kalır ve mevcut Group'lara sessizce izin eklemez. Phase 3 Employee izinleri (`DEC-024`): `accounts.view_employee`, `accounts.add_employee`, `accounts.change_employee`; `delete` expose edilmez; User-link editing `change_employee` kapsamındadır. **Açık kalan:** “Operasyonel depo işleri” ayrıntılı izin listesi (`DEC-OPEN-005`). | Phase 2/3 rol/kullanıcı–rol yönetimi geniş yorumlanamaz; operasyonel depo izinleri beklenir. Employee–User linkage `DEC-024` ile kararlıdır. |

## 13. Audit ve İzlenebilirlik Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| AUD-001 | CONFIRMED | Önemli her işlem, işlemi yapan kullanıcıya bağlanmalıdır. | Kullanıcısız önemli işlem kaydı kabul edilmemelidir. |
| AUD-002 | CONFIRMED | Denetim açısından önemli kayıtlar iş olayını ve sistemce kaydedilmiş zamanı korumalıdır. | Olayın ne olduğu ve ne zaman gerçekleştiği görülebilmelidir. |
| AUD-003 | CONFIRMED | İlgili olduğunda etkilenen malzeme/tekil varlık, lokasyon ve miktar olayla ilişkilendirilmelidir. | Stok etkisi olay bağlamından doğrulanabilmelidir. |
| AUD-004 | CONFIRMED | İlgili düzeltme talebi, karar ve düzeltme sonucu birbirine bağlanarak izlenebilmelidir. | Uçtan uca düzeltme izi kaybolmamalıdır. |
| AUD-005 | CONFIRMED | Tamamlanmış stok geçmişi normal operasyon kullanıcılarınca sessizce değiştirilemez veya silinemez. | Geçmiş bütünlüğü yetki testleriyle doğrulanmalıdır. |
| AUD-006 | CONFIRMED | Yetkili kullanıcılar işlem geçmişini görüntüleyebilmelidir. | Denetim için geçmiş erişilebilir olmalıdır. |
| AUD-007 | TBD DEPENDENCY | Ayrı audit günlüğü, olay kaydı veya başka teknik mekanizma seçimi bu belgenin kapsamı değildir. | Faz 0.3 veri ihtiyaçlarını tanımlar; uygulama mekanizmasını seçmez. |
| AUD-008 | TBD DEPENDENCY | Envanter geçmişi, audit kayıtları ve fotoğraflar için saklama/silme süreleri belirlenmemiştir. | Süre dolumuna bağlı silme kuralı henüz uygulanamaz. |

## 14. Minimum Stok Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| MIN-001 | CONFIRMED | Malzeme için yapılandırılabilir minimum stok seviyesi tanımlanabilmelidir. | Farklı malzemeler farklı eşiklere sahip olabilmelidir. |
| MIN-002 | CONFIRMED | Değerlendirilen stok yapılandırılmış minimum seviyenin altına düştüğünde malzeme düşük stok olarak işaretlenmelidir. | Eşiğin altındaki örnek görünür bir düşük stok sonucu üretmelidir. |
| MIN-003 | CONFIRMED | Düşük stok durumu uygun yetkili kullanıcılar için açıkça görünür olmalıdır. | Yetkili kullanıcı durumu rapor veya görünür liste üzerinden saptayabilmelidir. |
| MIN-004 | TBD DEPENDENCY | Minimum stok karşılaştırmasının tüm lokasyonların toplamına mı, belirli lokasyona mı ve hangi kondisyonlara göre yapılacağı belirlenmemiştir. | Toplam/lokasyon bazlı eşik varsayılamaz. |
| MIN-005 | TBD DEPENDENCY | Eşikle eşit stokta davranış ürün ifadesinde “altına düşme” olarak geçer; farklı uyarı politikası isteniyorsa ayrıca onaylanmalıdır. | Onaylı kural yalnızca stok minimumdan küçükken düşük stok sonucudur. |

## 15. Raporlama Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| REP-001 | CONFIRMED | Haftalık stok giriş miktarları raporlanabilmelidir. | Seçilen haftadaki girişler raporda görülebilmelidir. |
| REP-002 | CONFIRMED | Haftalık stok çıkış miktarları raporlanabilmelidir. | Seçilen haftadaki çıkışlar raporda görülebilmelidir. |
| REP-003 | CONFIRMED | Malzeme kullanım miktarları raporlanabilmelidir. | Kullanım verisi onaylanacak tanıma göre gruplanabilmelidir. |
| REP-004 | CONFIRMED | Stoğu en fazla azalan malzemeler görünür olmalıdır. | Azalış hesap yöntemi onaylandıktan sonra sıralama doğrulanabilmelidir. |
| REP-005 | CONFIRMED | Düşük stoktaki malzemeler görünür olmalıdır. | MIN kurallarıyla aynı sonuç kümesini üretmelidir. |
| REP-006 | CONFIRMED | Rapor verileri Excel'e dışa aktarılabilmelidir. | Görüntülenen/onaylanmış rapor kapsamı Excel çıktısında korunmalıdır. |
| REP-007 | TBD DEPENDENCY | Hafta sınırları, tarih aralığı, fabrika saat dilimi, “kullanım” tanımı, azalış hesabı, gruplama, filtreler ve rapor yetkileri belirlenmemiştir. | Kesin rapor toplamları bu kararlar olmadan tanımlanamaz. |

## 16. Excel Veri Aktarım Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| IMP-001 | CONFIRMED | Mevcut Excel envanteri doğrulanmadan doğru, eksiksiz veya yetkili kabul edilemez. | Dosyayı yüklemek tek başına güvenilir başlangıç stoğu oluşturamaz. |
| IMP-002 | CONFIRMED | Excel içe aktarımı kontrollü doğrulamayı desteklemelidir. | Geçersiz veya çözümlenmemiş veri sessizce yetkili stoğa dönüşmemelidir. |
| IMP-003 | CONFIRMED | İçe aktarım öncesinde alan eşleştirme, veri kalitesi ve mükerrer kayıt kontrolleri tanımlanmalıdır. | Doğrulama sonuçları başlangıç stok kararından önce ele alınmalıdır. |
| IMP-004 | CONFIRMED | Excel verisi, fiziksel sayım ve sisteme alınacak başlangıç bakiyeleri arasında mutabakat yapılmalıdır. | Başlangıç bakiyesi fiziksel gerçekle karşılaştırılmalıdır. |
| IMP-005 | CONFIRMED | Mutabakat onaylanmadan yeni sistem envanteri yetkili stok kaynağı ilan edilemez. | Yetkili duruma geçiş yalnızca tamamlanmış mutabakat sonrasında olabilir. |
| IMP-006 | TBD DEPENDENCY | Kaynak dosya yapısı, alan eşlemeleri, veri sahipleri, temizleme kuralları, aktarım şablonu ve hata çözüm süreci belirlenmemiştir. | Dosya görülmeden alan veya dönüşüm kuralı icat edilemez. |

## 17. Fiziksel Sayım ve Mutabakat Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| CNT-001 | CONFIRMED | Sistem fiziksel stok sayımı ve mutabakatını desteklemelidir. | Malzeme ve lokasyon bazında fiziksel sonuç kaydedilebilmelidir. |
| CNT-002 | CONFIRMED | Fiziksel sayım, malzemenin gerekli raf/lokasyon ayrıntısında gerçekten bulunup bulunmadığını doğrulayabilmelidir. | Sistem “mevcut” dediğinde fiziksel bulunabilirlik test edilebilmelidir. |
| CNT-003 | CONFIRMED | Sistem kaydı ile fiziksel sayım arasındaki fark kaydedilmelidir. | Fark sessizce stok değerinin üzerine yazılamaz. |
| CNT-004 | CONFIRMED | Sayım farkının stok etkisi yalnızca yetkili kontrollü düzeltme üzerinden uygulanmalıdır. | Yetkisiz sayım farkı stok bakiyesini değiştiremez. |
| CNT-005 | CONFIRMED | Sayım, tespit edilen fark, düzeltme talebi/kararı ve sonuç arasında izlenebilirlik korunmalıdır. | Mutabakat uçtan uca denetlenebilmelidir. |
| CNT-006 | CONFIRMED | İlk yetkili stok açılışından önce fiziksel sayım ve mutabakat tamamlanmalıdır. | Tamamlanmamış mutabakatla otorite devri yapılamaz. |
| CNT-007 | TBD DEPENDENCY | Sayım sıklığı, sayım sorumluları, kör sayım yöntemi, fark toleransı, onay seviyeleri ve mutabakat tamamlanma ölçütü belirlenmemiştir. | Ayrıntılı sayım iş akışı bu kararlar olmadan kesinleştirilemez. |

## 18. QR / Barkod Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| QR-001 | CONFIRMED | Sistem malzeme QR/barkod etiketlerini desteklemelidir. | Geçerli etiket ilgili malzemeyi çözümleyebilmelidir. |
| QR-002 | CONFIRMED | Sistem lokasyon QR/barkod etiketlerini desteklemelidir. | Geçerli etiket ilgili fiziksel lokasyonu çözümleyebilmelidir. |
| QR-003 | CONFIRMED | Bir QR/barkod tanımlayıcısı sistemde benzersiz biçimde belirlenebilir tek bir hedef nesneye çözülmelidir. | Aynı tanımlayıcı belirsiz biçimde iki nesneye yönlenemez. |
| QR-004 | CONFIRMED | Malzeme ve lokasyon QR/barkod desteği en geç nihai V1 üretim devreye alımından önce hazır ve doğrulanmış olmalıdır. | Üretim kabulünde iki nesne türü de taranarak bulunabilmelidir. |
| QR-005 | TBD DEPENDENCY | Yük biçimi, tanımlayıcı standardı, barkod türü, etiket ölçüsü/dayanıklılığı, yazıcı entegrasyonu ve yeniden basım kuralları belirlenmemiştir. | Etiket standardı sonraki tasarım ve ekipman doğrulamasına bağlıdır. |

## 19. Zaman ve Kayıt Kuralları

| Kural ID | Durum | Kural | Gerekçe / doğrulama etkisi |
|---|---|---|---|
| TIME-001 | CONFIRMED | İşlem zamanı sistem tarafından kaydedilmelidir. | Normal kullanıcının girdiği değer asıl işlem zamanı yerine geçemez. |
| TIME-002 | CONFIRMED | Normal operasyon kullanıcıları tamamlanmış işlemin özgün zamanını sessizce değiştiremez. | Zaman düzeltmesi gerekiyorsa denetlenebilir kontrollü süreç gerekir. |
| TIME-003 | CONFIRMED | Önemli kayıtlar oluşturma/işlem zamanını; düzeltme kararları ayrıca karar zamanını korumalıdır. | Olay ve karar sırası denetlenebilmelidir. |
| TIME-004 | CONFIRMED | İş raporları fabrikanın mutabık kalınmış yerel saat dilimini kullanmalıdır; fabrika Türkiye'de faaliyet göstermektedir. | Aynı iş dönemi tutarlı yerel zamanla raporlanmalıdır. |
| TIME-005 | TBD DEPENDENCY | Üretim saat dilimi tanımı, yaz saati/geçmiş zaman davranışı, hafta başlangıcı ve dönem kapanış kuralları teknik ve iş kararı olarak kesinleştirilmemiştir. | Kesin haftalık rapor sınırları bu karar olmadan sabitlenemez. |
| TIME-006 | TBD DEPENDENCY | Envanter geçmişi, audit kayıtları ve düzeltme fotoğrafları için saklama ve silme süreleri onaylanmamıştır. | Otomatik silme veya kalıcı saklama varsayılamaz. |

## 20. Açık İş Kararları / TBD

### Çelişki ve belirsizlik analizi

| ID | Tür | Konu | Sonuç |
|---|---|---|---|
| OD-001 | Belirsiz terminoloji | “Bozuk” ifadesinin kullanılabilir stok, minimum stok ve iade davranışına etkisi bilinmiyor. | COND-005 sonuçlandırılamaz. |
| OD-002 | Belirsiz terminoloji | “Çıkma sağlam” ifadesinin kondisyon mu, hareket olayı mı veya ikisi mi olduğu bilinmiyor. | Yeni hareket türü varsayılmaz. |
| OD-003 | Belirsiz terminoloji | “Çıkma bozuk” ifadesinin kondisyon mu, hareket olayı mı veya ikisi mi olduğu bilinmiyor. | Yeni hareket türü varsayılmaz. |
| OD-004 | Eksik karar | Hangi malzemelerin ve hangi koşullarda iade edilebildiği bilinmiyor. | Yalnızca iade desteği ve izlenebilirlik kesinleştirildi. |
| OD-005 | Eksik karar | Miktar bazlı malzemenin çoklu lokasyona dağıtım, toplama ve seçim kuralları bilinmiyor. | Çoklu lokasyon kapasitesi korunur; dağıtım kuralı konmaz. |
| OD-006 | Eksik karar | Minimum stok toplam stokla mı, lokasyonla mı ve hangi kondisyonlarla mı karşılaştırılacak bilinmiyor. | Düşük stok hesabı kesinleştirilemez. |
| OD-007 | Eksik karar | Üretim hattı ile fiili kullanım yeri arasındaki hiyerarşi ve veri kaynağı bilinmiyor. | İki alan da zorunlu kalır. |
| OD-008 | Eksik karar | Tekil varlığın zorunlu kimlik alanları bilinmiyor. | Yalnızca sistem içi benzersiz kimlik zorunludur. |
| OD-009 | Eksik karar | Depo Görevlisinin “operasyonel depo işleri” ayrıntılı izin listesi bilinmiyor. | Yetki genişletilerek yorumlanamaz. |
| OD-010 | Öneri bekliyor | Ret gerekçesinin zorunluluğu ürün belgesinde onaylanmamıştır. | COR-010 PROPOSED olarak kalır. |
| OD-011 | Eksik karar | Fiziksel sayım farkı için tolerans, onay seviyesi ve sorumlular bilinmiyor. | Yetkili düzeltme gereği korunur; akış kesinleşmez. |
| OD-012 | Eksik karar | Envanterin yetkili kaynağa dönüşmesi için mutabakatı kimin ve hangi tamamlanma ölçütüyle onaylayacağı bilinmiyor. | Onaylı mutabakat ön koşulu korunur. |
| OD-013 | Eksik karar | Transfer gereken senaryolar ve transfer yetkileri bilinmiyor. | Transfer yalnızca uygulanabilirliği doğrulanan senaryolar için kapsamdır. |
| OD-014 | Eksik karar | Birim dönüşümü, ondalık hassasiyet ve kısmi miktar davranışı bilinmiyor. | Otomatik dönüşüm varsayılmaz. |
| OD-015 | Eksik karar | Aktif stok/hareket geçmişi bulunan malzemenin takip modu değiştirilebilir mi bilinmiyor. | Değişiklik koşulu kesinleştirilemez. |
| OD-016 | Kararlı | İçinde stok bulunan lokasyonun pasifleştirme/silme süreci `DEC-023` ile kararlıdır. Hard delete yoktur. Non-zero stock pasifleştirilemez ve `can_hold_stock` True→False yapılamaz. Inventory entegrasyonu aynı kuralı otoritatif uygular. | Geçmiş referans korunur; mevcut stok sahipsiz kalmaz. |
| OD-017 | Eksik karar | Düzeltme talep eden ile karar veren aynı kişi olabilir mi; iptal/yeniden gönderim nasıl işler bilinmiyor. | Görev ayrılığı varsayılmaz. |
| OD-018 | Eksik karar | Düzeltme fotoğrafının güncellik, biçim, boyut, erişim ve saklama koşulları bilinmiyor. | Güncel fotoğraf zorunluluğu korunur. |
| OD-019 | Eksik karar | Rapor hafta sınırları, kullanım tanımı, azalış hesabı, filtre ve gruplamalar bilinmiyor. | Rapor türleri zorunlu, hesap ayrıntıları TBD'dir. |
| OD-020 | Eksik karar | Excel dosya yapısı, eşlemeler, temizleme ve hata çözüm süreci bilinmiyor. | Kaynak dosya analizi gereklidir. |
| OD-021 | Eksik karar | QR/barkod yükü, standardı ve yazıcı/etiket entegrasyonu bilinmiyor. | Benzersiz nesne çözümleme kuralı korunur. |
| OD-022 | Teknik bağımlılık | Negatif stoğun eş zamanlı işlemlerde nasıl atomik önleneceği sonraki teknik tasarıma aittir. | İş kuralı değişmez; uygulama yöntemi seçilmez. |
| OD-023 | Eksik karar | Fabrikanın mutabık kalınmış yerel saat dilimi yapılandırması ve hafta başlangıcı bilinmiyor. | Türkiye yerel iş zamanı yönü korunur. |
| OD-024 | Eksik karar | Envanter geçmişi, audit kayıtları ve fotoğrafların saklama/silme süreleri bilinmiyor. | Retention politikası varsayılmaz. |
| OD-025 | Eksik karar | Kategoriye özgü teknik alanlar ve zorunlulukları bilinmiyor. | Gerçek örnek ve Excel analizi beklenir. |
| OD-026 | Kısmen kararlı | Rol atama/onay süreci Phase 2 için `DEC-022` ile kararlıdır. Kullanıcı–Employee ilişkisi `DEC-024` ile kararlıdır: nullable one-to-one, ownership Employee, delete `SET_NULL`; sicil string/global unique/editable. | Employee foundation implementation Phase 3.2 COMPLETE; retention pilot öncesi kararları açık kalır. |
| OD-027 | Eksik karar | Kondisyon değişikliğinin kendisinin hangi kayıtlı iş olayıyla yapılacağı bilinmiyor. | Kondisyon sessizce değiştirilemez; olay türü varsayılmaz. |
| OD-028 | Eksik karar | Nicel stok doğruluğu hedefi ve kabul edilebilir sapma yaklaşımı bilinmiyor. | Fiziksel bulunabilirlik temel başarı yönüdür. |
| OD-029 | Zamanlama belirsizliği | QR/barkod V1 kapsamındadır; ara sürümdeki teslim anı belirtilmemiştir. | En geç nihai V1 üretim devreye alımından önce doğrulanmalıdır. |

Doğrudan çözülemez bir çelişki tespit edilmemiştir. QR/barkodun “V1 veya nihai üretim devreye alımı öncesi” ifadesi ile V1 kapsam listesi arasındaki zamanlama belirsizliği, ürün belgesindeki kabul yönü korunarak OD-029 altında açık bırakılmıştır.

## 21. Faz 0.3 İçin Girdi

Faz 0.3 veri modeli ve ilgili teknik tasarım çalışmaları aşağıdaki onaylı ayrımları korumalıdır:

1. Miktar bazlı malzeme ile benzersiz kimlikli tekil varlık aynı stok kavramı içinde birbirine karıştırılmamalıdır.
2. Malzeme takip modu malzeme bazında yapılandırılabilir olmalıdır.
3. Malzeme kondisyonu, hareket türünden ayrı temsil edilebilmelidir.
4. Stok, raf seviyesi dahil geçerli lokasyonla ilişkilendirilmeli; tekil varlık aynı anda iki fiziksel lokasyonda bulunamamalıdır.
5. Her miktar veya konum değişikliği denetlenebilir bir iş olayına dayanmalıdır.
6. Tamamlanmış işlem geçmişi korunmalı; düzeltme, orijinal işlem ve onay kararıyla ilişkilendirilmelidir.
7. Negatif stok tüm işlemlerde ve eş zamanlı kullanımda engellenmelidir; teknik yöntem sonraki tasarım kararıdır.
8. Stok çıkışı alıcı adı, soyadı, sicil numarası, üretim hattı, fiili kullanım yeri ve sistem zamanını taşımalıdır.
9. Kullanıcı, rol, işlem sahibi, karar sahibi ve ilgili zamanlar izlenebilir olmalıdır.
10. Excel başlangıç verisi, fiziksel sayım ve yetkili başlangıç bakiyesi birbirinden ayırt edilip mutabakatla bağlanabilmelidir.
11. Malzeme ve lokasyon QR/barkod tanımlayıcıları benzersiz bir sistem nesnesine çözülmelidir.
12. Minimum stok ve raporlama için OD-006, OD-019 ve OD-023 kararları verilmeden toplam/lokasyon veya dönem varsayımı yapılmamalıdır.
13. Saklama süreleri kesinleşmediği için geçmiş, audit ve fotoğraf kayıtlarının silinme davranışı varsayılmamalıdır.

Faz 0.3, bu belgedeki TBD bağımlılıklarını iş kararı verilmiş gibi kapatmamalı ve uygulama mimarisini iş kuralı olarak sunmamalıdır.
