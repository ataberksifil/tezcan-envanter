# UI Modernizasyon — Durum

**Başlangıç HEAD:** `fca1ad455ef203f40b09aafc25e15bed54619c25` (= origin/main)
**Güncel faz:** Faz 9 — Son bütünsel inceleme
**Plan:** [UI-POLISH-MASTER-PLAN.md](UI-POLISH-MASTER-PLAN.md) · **Tasarım:** [UI-DESIGN-SYSTEM.md](UI-DESIGN-SYSTEM.md)

## Tamamlanan fazlar

| Faz | Commit | Not |
|---|---|---|
| 1 — Temel sistem + prototip ekranlar | `8313adc` | Kabuk, ana sayfa, Mevcut Stok, Stok girişi, hata sayfaları |
| 2 — Malzeme bulma ve katalog | `2deb9b4` | Malzeme listesi (stok özeti), detay (raf dağılımı + satır "Taşı"), form (bölümler, model okutma Enter güvenliği), stok aramasında anahtar kelime + kelime sırasından bağımsız eşleşme |
| 3 — Stok hareket formları ve detayları | `3f9af87` | Giriş/çıkış/iade/transfer formları ve detayları, tekil varlık ekranları |
| 4 — Talep Takip ve SKT | `c6dcfa9` | Talep liste/detay (istenen/gelen/kalan + ilerleme), formlar; SKT listesi (giriş konumu açıkça), kontrol formu (üç sonuç açıklamalı); talep bağlantılı girişte SKT hatası düzeltildi |
| 5 — Hareket geçmişi ve düzeltmeler | `18b4382` | Hareket listesi/detayı (işaretli miktar, kaynak → hedef plakaları), düzeltme listesi/formu/detayı (fark görünümü, kanıt, karar paneli, tarayıcı onayı), okunur düzeltme seçenek etiketleri |
| 6 — Barkod tarama ve etiket | `f34959b` | Tarama sayfası (masaüstünde USB okuyucu, telefonda kamera önce; hata sonrası alan seçili), etiket sayfası (gerçek boyut önizleme, 104 mm kılavuz, yalnız etiket yazdırılır) |
| 7 — Sayım ve kesim | `aab5d70` | Oturum listesi/detayı (ilerleme), kör miktar sayımı (Enter sonraki satıra), tekil sayım, fark inceleme (beklenen → sayılan, yön), başlat/tamamla onayları, kesim listesi/hazırlık/uygula (tarayıcı onayı) |
| 8 — Yönetim | (bu commit) | Yönetim merkezi (gruplu liste), kategori/birim/lokasyon/çalışan/üretim hattı liste-detay-form (ortak `core/form_page.html`), lokasyon detayında önce "bu raftaki stok" + "Bu rafa yerleştir", roller/izinler/kullanıcılar |

## Faz 1 kapsamı

Tasarım sistemi (tokenlar, IBM Plex + Bootstrap Icons alt kümesi yerel), kabuk (4 gruplu menü, çentiksiz aktif durum, üst okut/ara, mobil 4 hedefli alt gezinme ve çekmecede hesap), ana sayfa (okut/ara → günlük işlemler → Takip), Mevcut Stok, Stok girişi (önizleme kilidi, seç+okut, Paket ve SKT açılır), 403/404/500, `|qty`, tarih widget ISO düzeltmesi, seçenek etiketlerinden gereksiz UUID, oturumda saklanan rol etiketi (sayfa başına ek sorgu yok).

## Faz 1 doğrulama

- Tam pytest: 1858 passed, 8 failed → 6 bilinen test-DB `MaterialCondition` seed kaybı (test DB'ye kanonik seed uygulanınca `test_material_condition.py` 24/24), 1 yeni rol testi (düzeltildi, `core/tests/test_ui_shell.py` 22/22), 1 önceden var olan flaky test `inventory/tests/test_expiry.py::test_outcomes_stay_in_history_and_do_not_clear_or_move_stock` (temiz HEAD `fca1ad4` üzerinde 10 koşuda 3 başarısız; aynı `recorded_at` + UUID eşitlik bozma; UI dışı, kapsam dışı).
- `manage.py check` temiz; `makemigrations --check` değişiklik yok; `git diff --check` temiz.
- Playwright: odak/okutma/`/` kısayolu/arama; 1440/1280/1024/768/390 yatay taşma 0; dolu mal kabul formunda Tab/Shift+Tab ile tüm alanlar sabit üst çubuk ve mobil eylem/alt gezinme çubuğu altında kalmadan görünür (`scroll-padding`); mobil üst arama kısa ipucu, kendi araması olan sayfada tekrar etmiyor.
- İngilizce "New" kondisyonu arayüz metni değil: dev DB'de kanonik olmayan 4 kayıt (`CD-xxxxxxxx`, `u-xxxx` kullanıcılarının stoğu). Veri değiştirilmedi.

## Açık konular / kararlar

1. **Talep Takip izinleri (dev DB)**: mevcut `STOREKEEPER` ve `ADMIN_MANAGER` Group'larında `procurement.view_purchaserequest` / `add_purchaserequest` / `change_purchaserequest` yok. Sessizce eklenmedi. Yönetim → Roller → rol izinleri ekranından verilebilir.
2. **Talep bağlantılı mal kabulde SKT**: form SKT alanını ve önizlemede SKT'yi gösteriyor, ancak `PurchaseRequestLineReceiveView` `expires_on`'u servise iletmiyor gibi görünüyor. Kullanıcı talimatı: Faz 4'te dar kapsamlı doğrula; gerçek hata ise mevcut servis mimarisiyle düzelt + test ekle; yeni SKT/lot sistemi yok.
3. **Miktar gösterimi**: kullanıcı onayladı (binlik nokta, ondalık virgül). `|qty` yeni ekranlara faz faz uygulanır; eski biçimi bekleyen 5 test ilgili ekranın fazında güncellenir.
4. **Talep Takip rol testleri**: Faz 4'te izole test kullanıcılarıyla yapılacak; dev Group'larına izin eklenmez (kullanıcı talimatı).

## Faz 2 doğrulama

- pytest: `catalog`, `inventory/tests/test_stock_list_ui.py`, yeni `inventory/tests/test_find_material_ui.py`, `identification` → yalnız bilinen 6 test-DB kondisyon seed hatası.
- Onaylı miktar biçimi nedeniyle güncellenen beklentiler: `inventory/tests/test_stock_list_ui.py` (malzeme detayı 5/2), `catalog/tests/test_material_ui.py` (minimum stok `12,5`).
- Playwright: malzeme listesi/detay/form 1440/1280/1024/768/390 taşma 0; "Taşı" → transfer formu malzeme+kaynak+kondisyon ön-dolu; model alanına HID okutma + Enter formu kaydetmedi. Dev DB'de atölye malzemeleri (teflon, 63A…) yok; bu aramalar otomatik testte sentetik veriyle doğrulandı.

## Faz 3 doğrulama

- Kapsam: stok girişi detayı ("Sonraki adım": Yerleştir / Transfer Et + etiket), tekil varlık girişi, transfer (kaynak sarı → hedef yeşil, iki rafa da okutma), stok çıkışı (ne/nereden ve kime/nerede bölümleri), iade, tüm hareket detayları, tekil varlık detayı ve varlık çıkış/iade/transfer formları; ortak `core/_form_fields.html` ve `_form_errors.html`; iade seçenek etiketinden UUID kaldırıldı, miktarlar Türkçe biçimde (`core/formatting.py`).
- Transfer ve çıkışta önizleme adımı yoktur (mevcut davranış: açık "…kaydet" düğmesi). Okuyucu Enter'ı bu formları göndermez.
- pytest: `inventory procurement identification core` → 918 passed; onaylı biçim nedeniyle güncellenen beklentiler: `test_quantity_return_ui.py` (10/4/6, `Kalan: 10 <birim>`), `test_quantity_transfer_ui.py` (4).
- Playwright: 12 ekran × 1440/390 → 200, yatay taşma 0, konsol hatası yok; transfer formunda HID okutma + Enter seçim yaptı, gönderim yok; boş çıkış formu sunucu doğrulamasıyla 7 alan hatası.

## Faz 4 doğrulama

- **Hata düzeltmesi (kullanıcı onaylı, dar kapsam):** talep bağlantılı adetli/tekil mal kabul, formda girilen SKT'yi servise iletmiyordu (önizleme "yok" gösteriyor, onayda SKT kayboluyordu). `procurement.services.receive_quantity_for_line` / `receive_serialized_for_line` isteğe bağlı `expires_on` alır ve mevcut `receive_quantity` / `receive_serialized` servislerine iletir (aynı transaction, aynı fingerprint kuralı). Önizleme SKT + uyarı gösterir. `inventory.services.expiry.expiry_warning_label` paylaşılan yardımcı oldu. Test: `procurement/tests/test_purchase_requests.py::test_linked_receipts_keep_the_entered_skt`.
- pytest: `procurement inventory core` → 808 passed.
- **İzole tarayıcı ortamı (UI sandbox):** dev Group'larına izin eklenmedi. Sentetik veri yalnız test DB'ye (`test_tezcan_envanter`) gerçek servislerle yüklendi (`ui.yonetici/ui.depocu/ui.teknisyen`, taze `setup_roles` şablonları), ikinci sunucu `localhost:8001` o DB ile çalıştı. Doğrulananlar: ana sayfa SKT sayıları; talep listesi/detay; talep bağlantılı mal kabul önizlemesi SKT + onay → kalem "Tamamlandı"; SKT kontrolü → "Stok değişmedi"; 1024/768/390 taşma 0; rol menüleri (teknisyende Talep yok, `/requests/` açıklamalı 403). Pytest öncesi test DB `flush` + kanonik kondisyon seed'i ile temizlendi.
- **Düzeltme (Faz 8'de doğrulandı):** Mevcut varsayılan `ADMIN_MANAGER` / `STOREKEEPER` Group'ları, yönetilebilir izin listesi dışında izin taşıdıkları için (örn. `inventory.view_stockbalance`, onay izinleri) uygulama içi rol yönetiminde "Kapsam dışı / salt okunur"dur (`accounts.services.access_management.is_supported_role`). Talep izinleri bu gruplara uygulama ekranından verilemez. Seçenekler (kullanıcı kararı): superuser ile Django Admin → Groups; ya da yönetilebilir izinlerden oluşan yeni bir rol (Yönetim → Roller → Yeni rol → İzinler: "Talep — görüntüleme/oluşturma/düzenleme") oluşturup kullanıcılara ek rol olarak atamak (bu yolun kendisi için `accounts.manage_access` gerekir; dev `demo.yonetici`'de yok).

## Faz 5 doğrulama

- Bulgu ve düzeltme: düzeltme formunda "Düzeltilecek satır" seçeneği `InventoryTransactionLine object (uuid)` gösteriyordu; artık "Satır N: KOD ad, miktar birim, kondisyon, KONUM kaynağından azalış / hedefine artış". Doğru malzeme/kondisyon seçenekleri stok formlarıyla aynı etiketleri kullanır (tekrarlı kod için kısa ayırt edici).
- pytest: `corrections inventory core catalog/tests/test_material_ui.py` → 921 passed.
- Sandbox (test DB, repo dışı medya kökü `ui_sandbox_settings`): depocu hareketten düzeltme talebi (−2, PNG kanıt) → yönetici detayında fark görünümü → tarayıcı onayı → APPROVED, bağlantılı "Kontrollü düzeltme" hareketi, UI-RAF-A1 bakiyesi 15 → 13; talep eden kendi talebinde karar düğmesi görmez; `data-confirm` iptalinde form gitmez. Hareket listesi mobil taşma 0.
- Not: pytest transactional testleri test DB'yi boşaltır; sandbox her tarayıcı turundan önce yeniden yüklenir, pytest öncesi `sandbox_reset` (flush + kanonik kondisyon seed) çalıştırılır.

## Faz 6 doğrulama

- Etiket geometrisi değişmedi: Code128 SVG doğal ölçüsü 88 × 17 mm (0,25 mm modül = 203 DPI'de 2 nokta), QR 46 mm; etiket kutusu 100 mm (≤ 104 mm). Yazdırma emülasyonunda (Playwright `emulateMedia print`) etiket 100 mm, kabuk/araç çubuğu gizli, taşma yok. Otomatik/sessiz yazdırma yok; `window.print()` kullanıcı eylemi.
- Tarama: sayfa açılışında odak okutma alanında; bilinmeyen kod → "Kodun işaret ettiği kayıt bulunamadı." + alan seçili; ardından okutulan geçerli TZ1L doğrudan konum kaydını açtı. Telefonda kamera bölümü üstte.
- pytest: `identification core` geçti.

## Faz 7 doğrulama

- Kör sayım korunur: sayan kişinin oturum detayı ve miktar sayım sayfasında "Beklenen" ve beklenen miktar yok (Playwright HTML kontrolü + mevcut testler).
- Enter sayım alanında sonraki satıra, son satırdan sonra "Sayılanları kaydet" düğmesine gider; hiçbir değer Enter ile kaydedilmedi (sandbox DB'de satırlar "Sayım bekliyor" kaldı).
- Sandbox: yönetici fark incelemesinde 250 → 238 farkını açıklamayla onayladı (tarayıcı onayı) → 1 COUNT_RECONCILIATION, bakiye 238. Kesim liste/hazırlık ekranları render; kesim uygulaması `data-confirm` ile korunur (sandbox'ta uygulanmadı).
- pytest: `counting imports core` + hareket geçmişi → 340 passed. Onaylı biçim nedeniyle güncellenen beklenti: `counting/test_ui.py` (8/7).
- Not: kişinin önceden girdiği sayım değeri `type=number` alanında tarayıcı yerel ayarıyla `15,000` görünür (Türkçe'de 15); sunucu değeri değişmez.

## Faz 8 doğrulama

- pytest: `accounts catalog locations core` + üretim hattı UI → 684 passed.
- Playwright (dev DB, `demo.yonetici`, yalnız görüntüleme): yönetim merkezi, kategori/birim/lokasyon/çalışan/üretim hattı liste + detay + yeni formları 1440/390 → 200, taşma 0, konsol hatası yok. `demo.yonetici`'de `accounts.manage_access` olmadığından roller/kullanıcılar 403 (beklenen; izin eklenmedi).
- Sandbox (test DB): `manage_access` yalnız sandbox kullanıcısına verildi; roller listesi, yeni rol formu, rol izinleri (31 yönetilebilir izin, ızgara), kullanıcılar ekranları 1440/390 taşma 0. Varsayılan roller "Kapsam dışı / salt okunur".
- "Yönetim" kırıntısı yalnız yönetim erişimi olan kullanıcıya görünür (mevcut testlerle korunur).

## Sonraki adım

Faz 9 — Son bütünsel inceleme (Impeccable kritiği, 1440/1280/1024/768/390, klavye/odak, yazdırma, rol bazlı kontrol, tam test paketi).
