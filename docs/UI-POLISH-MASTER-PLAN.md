# UI Modernizasyon Ana Planı

Kapsam: mevcut Django uygulamasının tüm uygulanmış ekranlarının görsel ve etkileşim modernizasyonu. Mimari (Django Templates + HTMX + vendored Bootstrap 5.3), servisler, migration'lar, DB guard'ları ve iş semantiği değişmez. Tasarım sözleşmesi: [UI-DESIGN-SYSTEM.md](UI-DESIGN-SYSTEM.md). İlerleme: [UI-POLISH-STATUS.md](UI-POLISH-STATUS.md).

## 1. Başlangıç değerlendirmesi (Stage A)

Gözlenen (Playwright, `demo.depocu` / `demo.yonetici`, 1440 ve 390 px):

- **Kabuk**: 14–16 bağlantılı düz üst menü iki satıra sarıyor; mobilde ekranın yarısını kaplıyor; aktif sayfa göstergesi, ikon, rol bilgisi yok.
- **Ana sayfa**: "Oturumunuz açık" dışında içerik yok; araştırmanın "ilk ekran arama/tarama olmalı" ilkesine aykırı.
- **Tipografi/renk**: Bootstrap varsayılanı; `app.css` 25 satır ve yinelenen kurallar; tasarım tokenı, ikon sistemi, favicon yok.
- **Tablolar**: düşük yoğunluk, çizgili satırlar, mobilde yatay kaydırma; konum yalnız kod metni; kondisyon düz metin; her satırda gereksiz "Aktif" rozetleri.
- **Miktar**: `80,000 ADET` — Türkçede virgül ondalık ayırıcı olduğundan belirsiz.
- **Formlar**: tüm alanlar tek düz liste; bölüm yok; isteğe bağlı alanlar belirtilmiyor.
- **Önizleme/onay**: önizleme ekranında alanlar düzenlenebilir kalıyor; kullanıcı önizlemeden sonra değeri değiştirip "Girişi kaydet"e basarsa değişen değer yeniden gözden geçirilmeden gönderilebiliyor.
- **Tarih alanları**: `type="date"` widget'larına `24/09/2026` biçiminde değer veriliyor; tarayıcı reddettiği için "Fiziksel geliş tarihi" varsayılanı boş görünüyor (7 widget).
- **Seçim listeleri**: malzeme/konum seçeneklerinde UUID gösteriliyor; okutma ile seçim yok (yalnız ayrı Tara sayfası).
- **Statik önbellek**: `app.css` sürümsüz; dağıtımdan sonra tarayıcı eski stili tutabiliyor.
- **Yetki**: mevcut dev `STOREKEEPER`/`ADMIN_MANAGER` Group'larında `procurement.*_purchaserequest` izinleri yok → Talep Takip menüsü görünmüyor, `/requests/` 403. `setup_roles` taze şablonlarda bu izinleri içerir; mevcut Group'lara sessizce eklenmez (bkz. STATUS "Açık konular").
- **Korunacak iyi desenler**: sunucu yetkisine göre menü, önizleme→onay akışı, `operation_id`, HTMX malzeme özeti, yerel vendored varlıklar, etiket yazdırma sayfaları.

## 2. Ekran envanteri

Durum: `—` başlanmadı · `PROTO` prototip · `DONE` tamam + tarayıcıda doğrulandı · `LIMIT` belgelenmiş sınır.

| # | Modül | Rota | Şablon | Kullanıcı / izin | Faz | Durum |
|---|---|---|---|---|---|---|
| 1 | core | kabuk (tüm sayfalar) | `base.html`, `core/_messages.html` | giriş yapmış | 1 | PROTO |
| 2 | auth | `/accounts/login/` | `registration/login.html` | anonim | 1 | PROTO |
| 3 | core | `/` | `core/home.html` | giriş yapmış | 1 | PROTO |
| 4 | core | 403/404/500 | `templates/403.html`, `404.html`, `500.html` | — | 1 | PROTO |
| 5 | inventory | `/inventory/stock/` | `stock_list.html` | `inventory.view_stockbalance` | 1 | PROTO |
| 6 | inventory | `/inventory/receipts/new/` (+ HTMX özet) | `receipt_form.html`, `_receipt_material_summary.html` | `inventory.receive_stock` | 1 | PROTO |
| 7 | catalog | `/catalog/materials/` | `material_list.html` | `catalog.view_material` | 2 | — |
| 8 | catalog | `/catalog/materials/<id>/` | `material_detail.html` | `catalog.view_material` | 2 | — |
| 9 | catalog | `/catalog/materials/new/`, `.../edit/` | `material_form.html`, `catalog/js/model_scan_helper.js` | `catalog.add_material` / `change_material` | 2 | — |
| 10 | inventory | `/inventory/receipts/<id>/` | `receipt_detail.html` | `inventory.view_inventorytransaction` | 3 | — |
| 11 | inventory | `/inventory/serialized-receipts/new/` | `serialized_receipt_form.html` | `inventory.receive_stock` | 3 | — |
| 12 | inventory | `/inventory/transfers/new/`, `/<id>/` | `transfer_form.html`, `transfer_detail.html` | `inventory.transfer_stock` | 3 | — |
| 13 | inventory | `/inventory/issues/new/`, `/<id>/` | `issue_form.html`, `issue_detail.html` | `inventory.issue_stock` | 3 | — |
| 14 | inventory | `/inventory/returns/new/`, `/<id>/` | `return_form.html`, `return_detail.html` | `inventory.return_stock` | 3 | — |
| 15 | inventory | `/inventory/serialized-assets/<id>/` (+ issue / return / transfer) | `serialized_asset_detail.html`, `serialized_issue_form.html`, `serialized_return_form.html`, `serialized_transfer_form.html` | view_stockbalance + hareket izinleri | 3 | — |
| 16 | procurement | `/requests/`, `/new/`, `/<id>/`, `/edit/`, kalem ekle/düzenle, malzeme bağla, kaleme mal kabul (qty/serialized) | `request_list/form/detail.html`, `line_form.html`, `link_form.html` (+ `receipt_form.html`) | `procurement.*_purchaserequest`, `inventory.receive_stock` | 4 | — |
| 17 | inventory | `/inventory/expiry-warnings/`, `/<id>/inspect/` | `expiry_warning_list.html`, `expiry_inspection_form.html` | view_inventorytransaction / `receive_stock` | 4 | — |
| 18 | inventory | `/inventory/transactions/`, `/<id>/` | `transaction_history_list.html`, `transaction_history_detail.html` | `inventory.view_inventorytransaction` | 5 | — |
| 19 | corrections | `/corrections/`, `/new/…`, `/<id>/`, approve/reject, evidence | `request_list/form/detail.html` | `corrections.*` | 5 | — |
| 20 | identification | `/identification/scan/`, `resolve/` | `scanner.html`, `identification/js/scanner.js` | view_material / view_stockbalance / view_location | 6 | — |
| 21 | identification | etiket (Code128 standart, QR kompakt) × Material/Asset/Location | `label.html`, `label.css` | ilgili view izni | 6 | — |
| 22 | counting | `/counts/` liste, yeni, detay, başlat, miktar sayımı, beklenmeyen, sayılmadı, tekil sayım, aday, eksik, tamamla, fark inceleme, onay/red | `session_list/form/detail.html`, `session_start_confirm.html`, `quantity_count.html`, `unexpected_quantity_form.html`, `serialized_count.html`, `candidate_serialized_form.html`, `session_complete_confirm.html`, `discrepancy_review.html` | `counting.*` | 7 | — |
| 23 | imports | `/baselines/`, yeni, detay, kesim | `baseline_list/form/detail.html` | `imports.establish_baseline` | 7 | — |
| 24 | core | `/management/` | `core/management.html` | yönetim erişimi | 8 | — |
| 25 | catalog | kategoriler, ölçü birimleri (liste/form/pasif-aktif) | `category_list/form.html`, `unit_list/form.html` | `catalog.*` | 8 | — |
| 26 | locations | `/locations/` liste, detay, form, pasif/aktif | `location_list/detail/form.html` | `locations.*` | 8 | — |
| 27 | accounts | çalışanlar liste/detay/form | `employee_list/detail/form.html` | `accounts.*_employee` | 8 | — |
| 28 | inventory | `/management/production-lines/…` | `production_line_list/detail/form.html` | `inventory.*_productionline` | 8 | — |
| 29 | accounts | roller, rol izinleri, kullanıcılar, kullanıcı rolleri | `role_list/form/permissions.html`, `user_list.html`, `user_roles.html` | `accounts.manage_access` | 8 | — |
| 30 | admin | `/admin/` | Django Admin | superuser/staff | — | LIMIT: kapsam dışı (kilitli koruma yüzeyi) |

Uygulanmamış ve **UI üretilmeyecek** backend işleri (PROJECT-SNAPSHOT §5): Excel import UI, raporlama, serialized correction, serialized `COUNT_RECONCILIATION`, paket dönüşümü, üretim staging konfigürasyonu.

## 3. Fazlar

Her faz: ortak bileşenleri yeniden kullanır; servis/migration/DB değiştirmez; ilgili pytest + Playwright masaüstü (1440) ve mobil (390) doğrulaması; tek mantıksal commit + push.

### Faz 1 — Temel sistem + prototip ekranlar
- **Hedef**: tokenlar, yerel font/ikon, kabuk (sol menü, üst arama, alt gezinme, rol), mesajlar, alan/okutma parçaları, `app.js` (TZ1 arama, çift gönderim, Enter güvenliği), giriş, ana sayfa, hata sayfaları, Mevcut Stok, Stok girişi (+ önizleme kilidi), tarih widget ISO düzeltmesi, `|qty` sunumu, statik sürüm parametresi.
- **Korunacak**: menü görünürlüğü = mevcut izin koşulları; menü etiket metinleri (testler); önizleme→onay; `operation_id`; HTMX malzeme özeti.
- **Kabul**: tüm sayfalar yeni kabukla render; 1440/1280/1024/768/390'da yatay taşma yok; okutma Enter ile gönderim yapmaz; önizlemede alan düzenlenemez; ilgili testler + yeni `ui` tag/`|qty`/tarih testleri geçer.
- **Kullanıcı revizyon kriterleri (onaylı)**: ana sayfa önceliği okut/ara → Mevcut Stok, Mal Kabul, Yerleştir / Transfer Et → yetkiye göre Talep Takip ve SKT; tek ortak okut/ara davranışı (TZ1 → çözümleyici, diğer metin → arama; okuyucu Enter'ı stok hareketi onaylamaz; hatalı/bilinmeyen/yetkisiz kod açık mesaj); açılışta ana alan odaklı, sonra odak çalınmaz; menüde sarı çentik/sol şerit yok, aktif durum satır zemini + güçlü yazı + vurgulu ikon, klavye odağı ayrı; menü 4 iş grubuna ayrılır, mobil alt gezinme 4 hedef; stok ve mal kabulde ikincil bilgi açılır bölümde; miktar Türkçe biçimde (binlik nokta, ondalık virgül).
- **Test**: `accounts/tests/test_auth.py`, `test_shell.py`, stok listesi ve mal kabul UI testleri, yeni `core/tests/test_ui_shell.py`.

### Faz 2 — Malzeme bulma ve katalog
- Malzeme listesi (kod/ad/marka/model/arama kelimesi, stok özeti), malzeme detayı (konum dağılımı plakalarla, kondisyon ayrı, hızlı eylemler: giriş/transfer/etiket), malzeme formu (bölümler, model barkod yardımcısı). Stok listesi aramasına `search_keywords` eklenir (salt okunur sorgu; test).
- **Kabul**: `teflon`, `bant`, `2.2k`, `24V`, `63A`, `encoder` aramaları tarayıcıda denenir.

### Faz 3 — Stok hareket formları ve detayları
- Giriş detayı (sonraki adımlar: etiket, yerleştir/transfer), tekil varlık girişi, transfer (kaynak→hedef `flow`, okutma), çıkış (alıcı/hat/kullanım yeri ayrımı), iade (orijinal çıkış bağlamı, kalan), tekil varlık detayı ve hareketleri. (Stok hareket formlarındaki seçenek UUID temizliği Faz 1'de `inventory/forms.py` yardımcılarıyla yapıldı; counting/catalog/locations formları kendi fazlarında.)
- **Korunacak**: kaynak azalır/hedef artar; staging → TRANSFER; yerleşmeden raf gösterilmez.

### Faz 4 — Talep Takip ve SKT
- Talep listesi/detayı (Talep No, kalemler, istenen/gelen/kalan, kısmi teslim), formlar, talep bağlantılı mal kabul; SKT listesi (yaklaşan vs geçmiş ayrı), kontrol formu (üç sonuç açıklamalı; stok otomatik düşmez uyarısı).

### Faz 5 — Hareket geçmişi ve düzeltmeler
- Hareket listesi (tür rengi, ±miktar, konum plakaları, filtre), hareket detayı, düzeltme listesi/talep formu (kanıt yükleme)/detay (fark görünümü, onay/red).

### Faz 6 — Barkod tarama ve etiket
- Tarama sayfası (HID alanı birincil, kamera ikincil), etiket sayfaları (DT-482 104 mm/203 DPI yazdırma önizlemesi, Code128 standart, QR kompakt).

### Faz 7 — Sayım ve kesim
- Oturum listesi/detayı (ilerleme), kör miktar sayımı (Enter ile sonraki satır), tekil sayım, beklenmeyen/aday, tamamla onayı, fark inceleme (eksik kırmızı / fazla mavi / uyan yeşil), kesim listesi/hazırlık/kesim.

### Faz 8 — Yönetim
- Yönetim merkezi, kategoriler, ölçü birimleri, konumlar (hiyerarşi, plakalar), çalışanlar, üretim hatları, roller/izinler/kullanıcılar.

### Faz 9 — Son bütünsel inceleme (Stage D)
- Impeccable ile tek kritik tur, 1440/1280/1024/768/390, klavye/odak, yazdırma, rol bazlı (ADMIN_MANAGER / STOREKEEPER / TECHNICIAN) doğrulama, tam pytest + `check` + `makemigrations --check` + `git diff --check`, önce/sonra görselleri.
