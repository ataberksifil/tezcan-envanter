# UI Modernizasyon — Durum

**Başlangıç HEAD:** `fca1ad455ef203f40b09aafc25e15bed54619c25` (= origin/main)
**Güncel faz:** Faz 4 — Talep Takip ve SKT
**Plan:** [UI-POLISH-MASTER-PLAN.md](UI-POLISH-MASTER-PLAN.md) · **Tasarım:** [UI-DESIGN-SYSTEM.md](UI-DESIGN-SYSTEM.md)

## Tamamlanan fazlar

| Faz | Commit | Not |
|---|---|---|
| 1 — Temel sistem + prototip ekranlar | `8313adc` | Kabuk, ana sayfa, Mevcut Stok, Stok girişi, hata sayfaları |
| 2 — Malzeme bulma ve katalog | `2deb9b4` | Malzeme listesi (stok özeti), detay (raf dağılımı + satır "Taşı"), form (bölümler, model okutma Enter güvenliği), stok aramasında anahtar kelime + kelime sırasından bağımsız eşleşme |
| 3 — Stok hareket formları ve detayları | (bu commit) | Giriş/çıkış/iade/transfer formları ve detayları, tekil varlık ekranları |

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

## Sonraki adım

Faz 4 — Talep Takip ve SKT (izole test kullanıcılarıyla; talep bağlantılı mal kabulde SKT doğrulaması).
