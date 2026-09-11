# Electrical Workshop Inventory & Material Tracking System

## 1. Document Purpose

Bu belge, fabrika Elektrik Atölyesi için geliştirilecek Envanter ve Malzeme Takip Sistemi'nin yetkili ürün gereksinimleri kaynağıdır. İş hedeflerini, V1 kapsamını, doğrulanmış gereksinimleri, kapsam dışı konuları, bağımlılıkları ve henüz karara bağlanmamış noktaları tanımlar.

Belge iş ve ürün gereksinimlerini tanımlar; teknik mimariyi, veri modelini veya uygulama teknolojilerini kesinleştirmez. Bu belgede:

- **Onaylı gereksinim**, sağlanan iş ihtiyacıyla doğrulanmış ve ürün kapsamına alınmış gereksinimi;
- **Tasarım yönü**, sonraki analiz ve tasarım çalışmalarını yönlendiren ancak ayrıntılı çözümü henüz kesinleştirilmemiş yaklaşımı;
- **TBD**, karar verilmesi, veri örneği görülmesi veya bir bağımlılığın netleşmesi gereken konuyu ifade eder.

## 2. Product Vision

Elektrik Atölyesi'nin malzemelerini, stok miktarlarını ve fiziksel konumlarını güvenilir biçimde gösteren; tüm önemli stok hareketlerini kullanıcıya bağlayan; hataların sessizce geçmişten silinmesini önleyen; günlük kullanımı sade ve denetlenebilir bir envanter sistemi oluşturmak.

Ürünün temel vaadi şudur:

> Sistem, bir malzemenin tanımlı bir depolama konumunda mevcut olduğunu söylüyorsa, malzeme fiziksel olarak o konumda bulunabilmelidir.

Görsel gelişmişlik bu hedefe göre ikincildir. V1, yalnızca Elektrik Atölyesi'nin operasyonel ihtiyaçlarına odaklanacaktır.

## 3. Business Problem

Mevcut envanter ağırlıklı olarak Excel ve manuel kayıtlarla yönetilmektedir. Yeni girişler ile malzeme çıkışlarının elle kaydedilmesi, özellikle atölyeden kullanılan veya çıkarılan malzemelerin stoktan düzenli düşülmemesine neden olmaktadır.

Bunun sonucu olarak sistemde veya tabloda mevcut görünen malzeme rafta bulunamayabilmektedir. Çözülmesi gereken birincil iş problemi, kayıtlı stok ile fiziksel stok arasındaki bu güven kaybıdır.

## 4. Success Criteria

- **SC-001 — Fiziksel doğruluk:** Sistemde belirli bir konumda mevcut görünen malzeme, gerekli depolama ayrıntı seviyesinde fiziksel olarak bulunabilmelidir.
- **SC-002 — Hareket disiplini:** Stok girişi, çıkışı, iadesi, gerekli transferi ve kontrollü düzeltmesi kayıt altına alınmadan stok durumu değişmemelidir.
- **SC-003 — Negatif stok engeli:** Hiçbir malzeme ve konum için izin verilen stok değeri sıfırın altına düşmemelidir.
- **SC-004 — İzlenebilirlik:** Önemli işlemlerin kim tarafından ve ne zaman yapıldığı izlenebilmelidir.
- **SC-005 — Değiştirilemez geçmiş:** Normal kullanıcılar geçmiş stok hareketlerini sessizce değiştirememeli veya silememelidir.
- **SC-006 — Konum doğruluğu:** Güncel stok, raf seviyesi dahil gerekli fiziksel konum bilgisiyle görüntülenebilmelidir.
- **SC-007 — Güvenilir başlangıç:** Eski Excel verisi doğrulanmadan doğru kabul edilmemeli; sistem stoğu yetkili kaynak ilan edilmeden önce fiziksel sayım ve mutabakat tamamlanmalıdır.
- **SC-008 — Operasyonel sadelik:** V1, Elektrik Atölyesi'nin günlük kullanımında uygulanabilir ve gereksiz gelecek özellikleriyle karmaşıklaştırılmamış olmalıdır.

Nicel doğruluk hedefleri, kabul edilebilir sapma oranı ve rapor performansı gibi ölçülebilir eşikler **TBD**'dir.

## 5. Current State

- Envanter yönetimi manuel yürütülmektedir.
- Yeni malzemeler manuel kaydedilmektedir.
- Malzeme çıkışları manuel kaydedilmektedir.
- Mevcut envanter verisi Excel'de tutulmaktadır.
- Aktif bir QR veya barkod tabanlı envanter sistemi yoktur.
- Etiket basım ekipmanı mevcuttur.
- Mevcut Excel dosyaları daha sonra temin edilip analiz edilecektir.
- Excel kayıtlarının doğruluğu ön kabul değildir.
- Yeni sistem yetkili stok kaynağı olmadan önce fiziksel sayım ve mutabakat gereklidir.

## 6. Users and Roles

Mimari ilke (`DEC-021`): envanter doğruluğunu tehlikeye atmadan güvenle yönetici tarafından yönetilebilecek referans veriler ve roller dinamik/yapılandırılabilir olmalıdır. `TECHNICIAN`, `STOREKEEPER`, `ADMIN_MANAGER` başlangıç rol şablonlarıdır; runtime authorization permission/policy tabanlı olmalıdır.

### Teknisyen

- **ROLE-001:** Stok bilgilerini görüntüleyebilmelidir.
- **ROLE-002:** Stok çıkışı yapabilmelidir.
- **ROLE-003:** Envanter ana verilerini serbestçe yönetememelidir.

### Depo Görevlisi

- **ROLE-004:** Stok girişi yapabilmelidir.
- **ROLE-005:** Stok çıkışı yapabilmelidir.
- **ROLE-006:** Yetkisi kapsamındaki operasyonel depo işlemlerini yapabilmelidir.

“Operasyonel depo işlemleri”nin ayrıntılı yetki matrisi **TBD**'dir.

### Yönetici / Müdür

- **ROLE-007:** Tam yönetim yetkisine sahip olmalıdır.
- **ROLE-008:** Düzeltme taleplerini onaylayabilmelidir.
- **ROLE-009:** Kontrollü düzeltme sürecini yönetebilmelidir.

### Ortak erişim ve kimlik gereksinimleri

- **SEC-001:** Sistem kimlik doğrulaması uygulamalıdır.
- **SEC-002:** Yetkiler rol bazlı olarak uygulanmalıdır.
- **SEC-003:** Tüm önemli işlemler işlemi yapan kullanıcıya bağlanmalıdır.
- **SEC-004:** Kullanıcı ve çalışan kimliği bilgileri desteklenmelidir.
- **SEC-005:** Normal kullanıcı, sistemce kaydedilen işlem zamanını sessizce değiştirememelidir.

Phase 2 rol atama ve erişim yönetimi politikası `DEC-022` ile kararlıdır (`accounts.manage_access`, güvenli catalog permission allowlist, anti-escalation, audited mutation). Kullanıcı hesabı ile çalışan sicil kaydı arasındaki ilişki (`DEC-HG-004`) ve Depo Görevlisi “operasyonel depo işleri” ayrıntılı izin matrisi (`DEC-OPEN-005`) **TBD**'dir.

## 7. Material Scope

### Onaylı başlangıç kapsamı

Bilinen üst düzey malzeme kategorileri:

- Otomasyon malzemeleri
- Elektrik / şalt malzemeleri
- Motorlar
- Komponentler
- X-Ray malzemeleri
- ShapeMeter malzemeleri
- Kablolar

- **MAT-001:** Sistem üst kategorileri ve gelecekte eklenecek alt kategorileri desteklemelidir.
- **MAT-002:** Farklı kategorilerin farklı teknik niteliklere ihtiyaç duyabileceği kabul edilmelidir.
- **MAT-003:** Kategoriye özel ayrıntılı teknik alanlar gerçek malzeme örnekleri ve Excel analizi sonrasında belirlenmelidir (**TBD**).
- **MAT-004:** Malzeme kataloğu esnek teknik nitelikleri desteklemelidir; ayrıntılı veri modeli sonraki tasarım görevine aittir.

### Takip biçimleri

- **MAT-005:** Sistem miktar bazlı malzemeleri desteklemelidir.
- **MAT-006:** Sistem tekil/seri numarasıyla izlenen varlıkları desteklemelidir.
- **MAT-007:** Takip biçimi yalnızca kategoriye kalıcı olarak bağlanmamalı, malzeme bazında yapılandırılabilmelidir.

Standart şalt malzemeleri, komponentler, kablolar ve terminaller miktar bazlı takibe; motorlar, sürücüler, PLC CPU'ları, HMI'lar ve pahalı veya onarılabilir özel ekipman tekil takibe örnek olabilir. Bu örnekler otomatik sınıflandırma kuralı değildir.

Tekil takipte zorunlu tanımlayıcılar, seri numarası kuralları ve takip biçiminin sonradan değiştirilme koşulları **TBD**'dir.

## 8. Core Functional Requirements

- **FR-001:** Sistem malzeme kataloğu, kategori ve alt kategori yönetimini desteklemelidir.
- **FR-002:** Sistem malzeme bazında esnek teknik nitelikleri desteklemelidir.
- **FR-003:** Sistem miktar bazlı ve tekil/seri bazlı stok takibini desteklemelidir.
- **FR-004:** Sistem hiyerarşik fiziksel konumları ve raf seviyesinde stok takibini desteklemelidir.
- **FR-005:** Yetkili kullanıcı güncel stoğu malzeme ve konum bağlamında görüntüleyebilmelidir.
- **FR-006:** Sistem stok girişi, stok çıkışı ve iadeyi desteklemelidir.
- **FR-007:** Operasyonel olarak gerekli olduğu doğrulandığında sistem konumlar arası transferi desteklemelidir; transferin zorunlu iş senaryoları **TBD**'dir.
- **FR-008:** Sistem malzeme durumunu kayıt altına alabilmelidir.
- **FR-009:** Sistem negatif stok oluşmasını önlemelidir.
- **FR-010:** Sistem işlem geçmişini saklamalı ve yetkili kullanıcılarca görüntülenebilir kılmalıdır.
- **FR-011:** Sistem açıklama ve güncel destekleyici fotoğraf içeren düzeltme talebi iş akışını desteklemelidir.
- **FR-012:** Düzeltmeler yönetici/müdür onayına tabi olmalıdır.
- **FR-013:** Sistem malzeme bazında yapılandırılabilir minimum stok seviyesi desteklemelidir.
- **FR-014:** Minimum seviyenin altına düşen stok, uygun yetkili kullanıcılar için açıkça görünür olmalıdır.
- **FR-015:** Sistem gerekli haftalık stok ve kullanım raporlarını sunmalıdır.
- **FR-016:** Sistem Excel'den kontrollü veri aktarımını ve Excel'e veri dışa aktarımını desteklemelidir.
- **FR-017:** Sistem fiziksel sayım ve mutabakat sürecini desteklemelidir.
- **FR-018:** Sistem malzeme QR/barkod etiketlerini desteklemelidir.
- **FR-019:** Sistem konum QR/barkod etiketlerini desteklemelidir.
- **FR-020:** Sistem yedekleme ve geri yüklemeye hazır olmalıdır; işletim yöntemi altyapı bağımlılıklarına göre daha sonra tanımlanacaktır.

## 9. Location Requirements

Bilinen depolama/atölye alanları:

- Elektrik Deposu
- Alkali Elektrik alanındaki kablo stoğu
- Enstrüman Atölyesi
- Bobinaj Atölyesi

- **LOC-001:** Tüm stok konum farkındalığıyla yönetilmelidir.
- **LOC-002:** Stok raf seviyesinde izlenebilmelidir.
- **LOC-003:** Konum yapısı gelecekte genişletilebilir bir hiyerarşiyi desteklemelidir.
- **LOC-004:** Bir malzemenin mevcut olduğu fiziksel konum, gerekli depolama ayrıntı seviyesinde bilinebilmelidir.
- **LOC-005:** Konumlar QR/barkod ile tanımlanabilmelidir.

Konum hiyerarşisinin katmanları, konum kodlama standardı, bir malzemenin birden fazla konumda tutulma kuralları ve sayım alanlarının sınırları **TBD**'dir. Bunlar sonraki iş kuralı ve veri modeli çalışmalarında kesinleştirilecektir.

## 10. Inventory Movement Requirements

### İş kavramları

Bilinen operasyon ve durum kavramları şunlardır:

- stok girişi,
- stok çıkışı,
- iade,
- hizmetten/servisten çıkarılan malzeme,
- iyi durumda kullanılmış/sökülmüş malzeme,
- arızalı malzeme,
- arızalı kullanılmış/sökülmüş malzeme.

- **MOV-001:** Stok miktarını veya tekil varlığın durum/konumunu etkileyen hareketler açıkça kayıt altına alınmalıdır.
- **MOV-002:** Hareket türü ile malzeme durumu sonraki tasarım fazlarında açık, ayrı ve yoruma kapalı biçimde modellenmelidir.
- **MOV-003:** Yukarıdaki kavramların işlem türü, malzeme durumu veya her ikisi olarak sınıflandırılması bu belgede kesinleştirilmemiştir (**TBD**).
- **MOV-004:** Hiçbir hareket negatif stok oluşturamamalıdır.
- **MOV-005:** İşlem zamanı sistem tarafından kaydedilmeli ve normal kullanıcılarca sessizce düzenlenememelidir.
- **MOV-006:** Her önemli hareket işlemi yapan kullanıcıya bağlanmalıdır.
- **MOV-007:** Transfer gerektiğinde kaynak ve hedef konum izlenebilir olmalıdır; ayrıntılı transfer kuralları **TBD**'dir.

### Stok çıkışında zorunlu bilgiler

- **ISS-001:** Teslim alan kişinin adı ve soyadı
- **ISS-002:** Teslim alan kişinin çalışan sicil numarası
- **ISS-003:** Sistemce kaydedilen işlem zamanı
- **ISS-004:** Malzemenin kullanıldığı üretim hattı
- **ISS-005:** Malzemenin fiili kullanım yeri

Üretim hattı ile fiili kullanım yerinin sözlükleri, hiyerarşik ilişkisi, zorunlu veri doğrulama biçimi ve teslim alan kişinin sistem kullanıcısı olmak zorunda olup olmadığı **TBD**'dir.

### Ayrıntılı modelleme sınırı

Hareket kayıtlarının muhasebe mantığı, iade ve düzeltmelerin ters kayıt yöntemi, seri bazlı hareket akışları, kısmi miktarlar ve birim dönüşümleri bu ürün belgesinde kesinleştirilmez. Bunlar sonraki iş kuralı ve veri modeli fazlarının konusudur.

## 11. Correction and Audit Requirements

- **AUD-001:** Hatalı envanter işlemleri sessizce değiştirilmemeli veya silinmemelidir.
- **AUD-002:** İşlemi yapan veya hatayı tespit eden kişi düzeltme talebi oluşturabilmelidir.
- **AUD-003:** Düzeltme talebi açıklama içermelidir.
- **AUD-004:** Düzeltme talebi güncel destekleyici fotoğraf içermelidir.
- **AUD-005:** Düzeltme talebi yönetici/müdür onayını beklemelidir.
- **AUD-006:** Kontrollü düzeltme yalnızca yetkili yönetici/müdür süreci üzerinden sonuçlandırılmalıdır.
- **AUD-007:** Talebin oluşturulması, değerlendirilmesi, onayı/reddi ve stok üzerindeki sonucu denetlenebilir olmalıdır.
- **AUD-008:** Önemli işlemlerin kullanıcı ve zaman bilgisi korunmalıdır.

Düzeltmenin ters hareket, dengeleme kaydı veya başka bir kontrollü mekanizma ile uygulanacağı; ret/iptal durumları; onaylayan ile talep edenin aynı kişi olup olamayacağı; fotoğraf saklama ve erişim politikası **TBD**'dir.

Düzeltme kanıtı dışındaki genel fotoğraf geçmişi ve montaj/kullanım yeri fotoğrafçılığı onaylanmış kapsam değildir ve **TBD** olarak kalır.

## 12. QR / Barcode Requirements

- **QR-001:** Aktif bir QR/barkod sistemi bulunmadığı için yeni tanımlama ve etiketleme süreci oluşturulmalıdır.
- **QR-002:** Malzemeler için QR veya barkod desteği sağlanmalıdır.
- **QR-003:** Fiziksel konumlar için QR veya barkod desteği sağlanmalıdır.
- **QR-004:** Mevcut etiket basım ekipmanının kullanılabilirliği sonraki teknik doğrulamada değerlendirilmelidir.
- **QR-005:** QR/barkod desteği en geç nihai V1 üretim devreye alımından önce hazır olmalıdır.

QR içeriği, benzersiz tanımlayıcı standardı, barkod türü, etiket boyutu/dayanıklılığı, yazıcı uyumluluğu ve yeniden etiketleme süreci **TBD**'dir.

## 13. Reporting Requirements

- **REP-001:** Sistem minimum stok seviyesinin altındaki malzemeleri ve düşük stok uyarılarını göstermelidir.
- **REP-002:** Haftalık stok giriş miktarları raporlanabilmelidir.
- **REP-003:** Haftalık stok çıkış miktarları raporlanabilmelidir.
- **REP-004:** Malzeme kullanım miktarları raporlanabilmelidir.
- **REP-005:** Stoğu en fazla azalan malzemeler görünür olmalıdır.
- **REP-006:** Raporların Excel'e aktarımı tercih edilen çıktı biçimidir ve V1 Excel dışa aktarma kapsamıyla desteklenmelidir.

Rapor filtreleri, dönem başlangıç/bitiş kuralları, “kullanım” ile “stok çıkışı” arasındaki ayrım, minimum stok uyarısının bildirim kanalı, rapor erişim yetkileri ve “en fazla azalan” sıralamasının hesap yöntemi **TBD**'dir.

Dekoratif veya ileri düzey gösterge paneli çalışmaları V1 taahhüdü değildir.

## 14. Data Migration Requirements

- **MIG-001:** Mevcut Excel verisi daha sonra temin edilmeli ve içeriği analiz edilmelidir.
- **MIG-002:** Excel verisi otomatik olarak doğru veya eksiksiz kabul edilmemelidir.
- **MIG-003:** İçe aktarım öncesinde alan eşleştirme, veri kalitesi ve mükerrer kayıt kontrolleri tanımlanmalıdır.
- **MIG-004:** Sistem kontrollü Excel içe aktarımını desteklemelidir.
- **MIG-005:** Yeni stok yetkili kaynak ilan edilmeden önce fiziksel stok sayımı yapılmalıdır.
- **MIG-006:** Excel kayıtları, fiziksel sayım ve sisteme alınacak başlangıç bakiyeleri arasında mutabakat yapılmalıdır.
- **MIG-007:** Mutabakat sonucu onaylanmadan sistem stoğu yetkili kabul edilmemelidir.

Excel dosyalarının yapısı, veri sahipleri, temizleme kuralları, aktarım şablonu, başlangıç bakiyesi onay yetkilisi, fiziksel sayım yöntemi ve kabul edilebilir sapma yaklaşımı **TBD**'dir.

## 15. Infrastructure Assumptions

### Onaylı yön

- **INF-001:** Nihai üretim sistemi yerel/fabrika tarafından barındırılan bir ortamda çalışacak şekilde hedeflenmektedir.
- **INF-002:** Sistem yedekleme ve geri yükleme hazırlığını desteklemelidir.

### Doğrulanması gereken bağımlılıklar

Aşağıdakiler doğrulanmış altyapı özellikleri değildir ve **TBD**'dir:

- üretim sunucusu işletim sistemi,
- CPU, RAM ve disk kapasitesi,
- Docker çalıştırma izni,
- PostgreSQL çalıştırma izni,
- kurum içi HTTPS kullanılabilirliği,
- yedekleme altyapısı ve işletim sorumluluğu,
- telefon/tablet cihazlarının fabrika ağına erişimi.

Bu bilinmeyenlerden herhangi biri, belirli bir teknolojiye verilmiş ürün kararı olarak yorumlanmamalıdır.

## 16. V1 Scope

V1 ürün kapsamı:

- kimlik doğrulama,
- rol bazlı erişim,
- kullanıcılar ve çalışan kimliği,
- malzeme kataloğu,
- kategoriler ve gelecekte alt kategoriler,
- esnek teknik nitelikler,
- miktar bazlı ve seri/tekil takip,
- hiyerarşik konumlar,
- raf seviyesi envanter,
- güncel stok,
- stok girişi,
- stok çıkışı,
- iadeler,
- gerekli olduğu doğrulanan transferler,
- malzeme durumu,
- negatif stok önleme,
- işlem geçmişi,
- düzeltme talebi iş akışı,
- düzeltme için fotoğraf kanıtı,
- yönetici/müdür onayı,
- denetlenebilirlik,
- minimum stok uyarıları,
- haftalık envanter raporları,
- kullanım raporları,
- Excel içe aktarma,
- Excel dışa aktarma,
- fiziksel sayım ve mutabakat,
- malzeme QR/barkodu,
- konum QR/barkodu,
- yedekleme ve geri yükleme hazırlığı.

V1 yalnızca Elektrik Atölyesi içindir. V1 kapsamındaki her başlığın ayrıntılı iş kuralları, veri modeli ve kabul senaryoları ilgili sonraki fazlarda netleştirilecektir.

## 17. Explicitly Out of Scope

Aşağıdakiler mevcut V1 taahhüdü değildir:

- SAP entegrasyonu,
- Mekanik Atölye,
- fabrika geneli envanter,
- yerel Android uygulaması,
- yerel iOS uygulaması,
- tam çevrimdışı öncelikli senkronizasyon sistemi,
- yapay zekâ ile malzeme tanıma,
- tahmine dayalı envanter,
- CMMS / eksiksiz bakım yönetimi,
- Kubernetes,
- mikroservis mimarisi,
- dekoratif veya ileri düzey gösterge paneli çalışmaları.

Bu maddeler ancak ileride ayrıca değerlendirilip onaylanırsa kapsama alınabilir.

## 18. Future Considerations

Onaylanmış V1 taahhüdü oluşturmayan gelecek değerlendirmeleri:

- Telefon veya mobil cihazlardan stok giriş/çıkış işlemleri
- Mobil cihazla QR/barkod tarama
- Bağlantı yokken işlemleri geçici olarak tutma ve merkezi fabrika sunucusu erişilebilir olduğunda gönderme
- Elektrik Atölyesi dışındaki bölümlere olası genişleme
- Düzeltme kanıtı dışında fotoğraf geçmişi veya montaj/kullanım yeri fotoğrafları

- **FUT-001 — Tasarım yönü:** V1 mimarisi, gelecekteki mobil ve sınırlı bağlantı senaryolarını gereksiz biçimde engellememelidir.
- **FUT-002 — Kapsam sınırı:** Tam çevrimdışı öncelikli mimari ve yerel mobil uygulama V1'in çekirdek kapsamı değildir.
- **FUT-003 — Sadelik:** Gelecek olasılıkları V1'in operasyonel akışlarını gereksiz yere karmaşıklaştırmamalıdır.

## 19. Product Principles

1. Fiziksel envanter doğruluğu birincil hedeftir.
2. Veri bütünlüğü görsel gelişmişlikten daha önemlidir.
3. Envanter geçmişi denetlenebilir kalmalıdır.
4. Kullanıcılar envanter geçmişini sessizce yeniden yazamamalıdır.
5. Negatif stok önlenmelidir.
6. Malzemenin fiziksel konumu gerekli depolama ayrıntı seviyesinde bilinmelidir.
7. Mevcut Excel envanteri körü körüne güvenilmek yerine doğrulanmalıdır.
8. Yeni stok yetkili kabul edilmeden önce fiziksel mutabakat yapılmalıdır.
9. İlk sürüm operasyonel olarak sade kalmalıdır.
10. Gelecek özellikleri V1'i gereksiz yere karmaşıklaştırmamalıdır.

## 20. Open Questions / TBD

### Açık Konular ve Bağımlılıklar

| ID | Açık konu / bağımlılık | Durum ve gerekli netleştirme |
|---|---|---|
| TBD-001 | Kategoriye özel teknik nitelikler | Gerçek malzeme örnekleri ve Excel analizi sonrasında belirlenecek. |
| TBD-002 | Esnek nitelik ve kategori ayrıntı modeli | Sonraki veri modeli fazında tasarlanacak. |
| TBD-003 | Tekil/seri takibin ayrıntıları | Zorunlu tanımlayıcılar, seri kuralları ve takip biçimi değişiklik koşulları belirlenecek. |
| TBD-004 | Hareket türü ve malzeme durumu ayrımı | Bilinen kavramların işlem türü, durum veya ikisi olarak sınıflandırılması resmi iş kurallarında netleştirilecek. |
| TBD-005 | İade, söküm, arıza ve düzeltme mekanikleri | Ters/dengeleyici hareketler ile miktar ve seri bazlı etkiler sonraki fazlarda belirlenecek. |
| TBD-006 | Transfer gereksinimi | Hangi operasyonlarda transferin zorunlu olduğu ve kaynak/hedef kuralları doğrulanacak. |
| TBD-007 | Ölçü birimleri ve kısmi miktarlar | Kablo gibi malzemeler dahil birim ve dönüşüm kuralları belirlenmedi. |
| TBD-008 | Konum hiyerarşisi | Katmanlar, kodlama, çoklu konum ve sayım alanı sınırları belirlenecek. |
| TBD-009 | Üretim hattı ve fiili kullanım yeri | Sözlükler, aralarındaki ilişki ve doğrulama biçimi belirlenecek. |
| TBD-010 | Teslim alan kişi ve kullanıcı ilişkisi | Teslim alan kişinin sistem kullanıcısı olma zorunluluğu ve çalışan kaynağı netleştirilecek. |
| TBD-011 | Rol ve yetki matrisi | Operasyonel depo görevleri, yönetici yetkileri, rol atama ve onay sınırları ayrıntılandırılacak. |
| TBD-012 | Düzeltme onay kuralları | Talep eden/onaylayan ayrımı, ret, iptal ve uygulama mekanizması belirlenecek. |
| TBD-013 | Düzeltme fotoğrafı politikası | Dosya biçimi, boyut, saklama, erişim ve “güncel” kanıt ölçütü belirlenecek. |
| TBD-014 | Daha geniş fotoğraf kapsamı | Fotoğraf geçmişi ile montaj/kullanım yeri fotoğrafları henüz onaylı değildir. |
| TBD-015 | QR/barkod standardı | Yük, benzersiz kimlik, semboloji, etiket biçimi/dayanıklılığı ve yeniden basım süreci tasarlanacak. |
| TBD-016 | Etiket ekipmanı uyumluluğu | Mevcut yazıcı ve sarf malzemeleri teknik olarak doğrulanacak. |
| TBD-017 | Rapor ayrıntıları | Filtreler, dönem sınırları, kullanım tanımı, erişim ve azalış sıralaması hesap yöntemi belirlenecek. |
| TBD-018 | Minimum stok uyarı yöntemi | Görünürlük ve olası bildirim kanalları ile yetkiler belirlenecek. |
| TBD-019 | Excel kaynak yapısı ve kalitesi | Dosyalar, alanlar, sahiplik, mükerrerler ve veri temizleme gereksinimleri analiz edilecek. |
| TBD-020 | Fiziksel sayım ve başlangıç mutabakatı | Sayım yöntemi, sorumlular, onay yetkilisi ve sapma yaklaşımı belirlenecek. |
| TBD-021 | Nicel başarı/kabul eşikleri | Stok doğruluğu hedefi ve izin verilebilecek sapma dahil ölçütler belirlenmedi. |
| TBD-022 | Üretim sunucusu işletim sistemi | Altyapı sahibi tarafından doğrulanacak. |
| TBD-023 | Sunucu CPU, RAM ve disk | Kapasite ve büyüme beklentisine göre doğrulanacak. |
| TBD-024 | Docker çalıştırma izni | Fabrika altyapı politikası tarafından doğrulanacak; ürün gereksinimi değildir. |
| TBD-025 | PostgreSQL çalıştırma izni | Fabrika altyapı politikası tarafından doğrulanacak; onaylı teknoloji varsayımı değildir. |
| TBD-026 | Kurum içi HTTPS | Sertifika ve iç ağ desteğinin varlığı doğrulanacak. |
| TBD-027 | Yedekleme altyapısı | Hedef, sıklık, saklama, sorumluluk ve geri yükleme testi yaklaşımı belirlenecek. |
| TBD-028 | Telefon/tablet ağ erişimi | Gelecek mobil kullanım için fabrika ağı erişimi doğrulanacak. |
| TBD-029 | Çevrimdışı işlem davranışı | V1 dışıdır; gelecekte saklama, çakışma ve senkronizasyon kuralları ayrıca tasarlanacak. |
| TBD-030 | Performans ve operasyonel hizmet seviyeleri | Kullanıcı sayısı, veri hacmi, yanıt süresi ve erişilebilirlik hedefleri verilmemiştir. |

### Tespit Edilen Belirsizlikler

- “Transferler gerektiğinde” ifadesi transfer desteğini V1 yönü olarak belirtmekte, ancak hangi iş senaryolarının transfer gerektirdiğini tanımlamamaktadır.
- “Hizmetten çıkarılan”, “iyi durumda kullanılmış/sökülmüş”, “arızalı” ve “arızalı kullanılmış/sökülmüş” kavramlarının hareket türü mü, malzeme durumu mu olduğu kesin değildir.
- QR/barkod desteği “V1 veya nihai üretim devreye alımından önce” istenirken V1 kapsam listesinde malzeme ve konum QR'ı yer almaktadır. Bu belgede, kapsam taahhüdü korunarak desteğin en geç nihai V1 üretim devreye alımında hazır olması yönü benimsenmiştir; ara sürüm zamanlaması **TBD**'dir.
- “Haftalık envanter raporları” için takvim haftası, saat dilimi ve dönem kapanış yaklaşımı tanımlanmamıştır.
- “Fiili kullanım yeri” ile “üretim hattı” arasındaki veri ilişkisi tanımlanmamıştır.
- “Yedekleme ve geri yükleme hazırlığı” V1 kapsamındadır; ancak altyapı, sorumluluk ve hizmet seviyesi henüz doğrulanmamıştır.

Sağlanan gereksinimler arasında doğrudan çözülemez bir çelişki tespit edilmemiştir. Yukarıdaki ifadeler ayrıntılandırılması gereken kapsam veya terminoloji belirsizlikleridir.

## 21. Acceptance Direction

V1 kabul yaklaşımı aşağıdaki yönlere dayanmalıdır:

- **ACC-001:** Yetkili kullanıcılar kimlik doğrulaması ve rollerine uygun erişimle çalışabilmelidir.
- **ACC-002:** Miktar bazlı ve tekil/seri bazlı örnek malzemeler tanımlanıp izlenebilmelidir.
- **ACC-003:** Malzeme, hiyerarşik konum ve raf bağlamında bulunabilmelidir.
- **ACC-004:** Giriş, çıkış, iade ve doğrulanmış gerekli transfer senaryoları stok ve işlem geçmişine doğru yansımalıdır.
- **ACC-005:** Stok çıkışı, teslim alan kişinin adı/soyadı ve sicil numarası ile üretim hattı, fiili kullanım yeri ve sistem işlem zamanı olmadan tamamlanmamalıdır.
- **ACC-006:** Negatif stok oluşturan işlem reddedilmelidir.
- **ACC-007:** Geçmiş hareket normal kullanıcı tarafından sessizce değiştirilememeli; hata, açıklama ve güncel fotoğraflı talep ile yönetici/müdür onayına gitmelidir.
- **ACC-008:** Minimum stok, haftalık giriş/çıkış, kullanım ve en fazla azalan stok görünürlüğü doğrulanmalıdır.
- **ACC-009:** Excel içe/dışa aktarım ile fiziksel sayım ve mutabakat süreci doğrulanmalıdır.
- **ACC-010:** Malzeme ve konum QR/barkod desteği nihai üretim devreye alımından önce doğrulanmalıdır.
- **ACC-011:** Önemli işlemlerin kullanıcı ve sistem zamanı üzerinden denetlenebilirliği gösterilmelidir.
- **ACC-012:** Fiziksel mutabakat onaylanmadan yeni sistem stoğu yetkili kaynak ilan edilmemelidir.
- **ACC-013:** Yedekleme ve geri yükleme hazırlığı, doğrulanacak üretim altyapısına uygun kabul senaryosuyla gösterilmelidir.

Ayrıntılı test senaryoları, nicel eşikler ve kabul sorumluları ilgili iş kuralı, veri modeli, altyapı ve test planı fazlarında belirlenecektir.

## Faz 0.2 İçin Girdi

Aşağıdaki onaylı gereksinimler bir sonraki görevde resmi, test edilebilir iş kurallarına dönüştürülmelidir:

1. **Stok doğruluğu ve otorite**
   - Sistem stoğunun fiziksel konumda bulunabilirliği.
   - Negatif stoğun her durumda engellenmesi.
   - Excel verisinin doğrulanmadan güvenilir kabul edilmemesi.
   - Fiziksel sayım ve mutabakat tamamlanmadan sistem stoğunun yetkili ilan edilmemesi.

2. **Malzeme takip biçimi**
   - Miktar bazlı ve tekil/seri bazlı takibin davranışları.
   - Takip biçiminin malzeme bazında yapılandırılması.
   - Seri numaralı varlıkların giriş, çıkış, iade, durum ve konum kuralları.

3. **Konum ve stok sahipliği**
   - Raf seviyesine kadar konum zorunluluğu.
   - Konum hiyerarşisi, kaynak/hedef ve çoklu konum davranışı.
   - Transfer gerektiren iş olayları.

4. **Stok hareketleri**
   - Giriş, çıkış, iade, transfer ve hizmetten/sökümden gelen malzeme akışları.
   - Hareket türü ile malzeme durumunun açık ayrımı.
   - Miktar, birim, kısmi kullanım ve stok etkisi kuralları.
   - Sistem işlem zamanının üretilmesi ve normal kullanıcı tarafından değiştirilememesi.

5. **Stok çıkışı**
   - Teslim alan ad/soyad ve sicil numarasının zorunluluğu.
   - Üretim hattı ve fiili kullanım yerinin zorunluluğu.
   - Teslim alan çalışan ile işlemi yapan kullanıcı arasındaki ilişki.

6. **Roller ve yetkilendirme**
   - Teknisyen, Depo Görevlisi ve Yönetici/Müdür için işlem bazlı yetki matrisi.
   - Ana veri yönetimi ve operasyonel depo işlemlerinin sınırları.
   - Tüm önemli işlemlerin kullanıcıya atfedilmesi.

7. **Düzeltme ve denetim**
   - Geçmiş hareketin sessizce değiştirilememesi.
   - Düzeltme talebinde açıklama ve güncel destekleyici fotoğraf zorunluluğu.
   - Yönetici/müdür onayı, ret ve uygulama akışı.
   - Düzeltmenin stok geçmişine denetlenebilir biçimde yansıması.

8. **Minimum stok ve raporlama**
   - Malzeme bazında minimum stok tanımı ve altına düşme davranışı.
   - Haftalık giriş/çıkış, kullanım ve en fazla azalan malzeme hesap kuralları.
   - Excel dışa aktarma kapsamı.

9. **Excel geçişi ve mutabakat**
   - İçe aktarım doğrulama, hata, mükerrer ve alan eşleştirme kuralları.
   - Fiziksel sayım, fark çözümü, başlangıç bakiyesi ve nihai onay süreci.

10. **QR/barkod operasyonu**
    - Malzeme ve konum kimliklerinin benzersizliği.
    - Etiket oluşturma, basma, tarama, kayıp/hasar ve yeniden basım kuralları.

Bu iş kuralları hazırlanırken bu belgede **TBD** olarak işaretlenen konular yanıtlanmadan varsayım üretilmemelidir.
