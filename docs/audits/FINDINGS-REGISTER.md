# Bulgu Kaydı

Tüm denetim skill'lerinin ortak bulgu kaydı. Bir bulgu tek kez kaydedilir; ilgili diğer skill'ler "İlgili" alanına yazılır.

Kod tabanı: HEAD `26496d9` + commit edilmemiş yerel çalışma ağacı (bkz. `AUDIT-RUNBOOK.md` "Kod tabanı durumu"). "uncommitted" / `(yerel)` işaretli uygulamalar HEAD'de yoktur.

## Alanlar ve değerler

| Alan | Değerler / içerik |
|---|---|
| Kimlik | `UI-`, `UX-`, `PERF-`, `BE-`, `ARCH-`, `SEC-`, `ENV-`, `DOC-` + sıra no |
| Tür | `DEFECT` (kanıtlanmış hata) · `PROPOSAL` (iyileştirme önerisi) · `ENV` (ortam/altyapı engeli) · `DOC` (belge tutarsızlığı) |
| Kapsam | Ekran grubu / modül / dosya / senaryo kimliği (`SCENARIO-MATRIX`) |
| Sahip skill | Kaydı açan ve kapatmaktan sorumlu skill |
| Risk | `CRITICAL` (stok doğruluğu, veri kaybı, yetki ihlali) · `HIGH` · `MEDIUM` · `LOW` |
| Kesin kanıt | Komut + çıktı, `dosya:satır`, ekran görüntüsü yolu, sahip ifadesi (tarihli). Ortam etiketi zorunlu |
| Yeniden üretim | Adım adım; rol, veri, ortam |
| Beklenen / Gerçekleşen | Kural kimliğiyle beklenen; gözlenen |
| Önerilen çözüm | Yalnız öneri; uygulanmaz |
| Bağımsız doğrulama | `NOT_STARTED` · `VERIFIED` (kim/nasıl) · `NOT_REPRODUCED` |
| Kullanıcı kabul durumu | `N/A` · `AWAITING_OWNER` · `OWNER_REJECTED` · `OWNER_ACCEPTED` (tarih + ifade) |
| Durum | `OPEN` · `IN_PROGRESS` · `FIXED_UNVERIFIED` · `CLOSED` · `WONT_FIX` (gerekçe) |
| İlgili | Diğer bulgular, kararlar (`DEC-…`), skill'ler |

Kapanış kuralı: `DEFECT` yalnız düzeltme **ve** bağımsız doğrulama (`VERIFIED`) sonrası `CLOSED` olur. Kullanıcıya görünen değişiklikte ayrıca `OWNER_ACCEPTED` gerekir.

---

## UI-001 — Masaüstü kenar çubuğu daraltma tasarımı sahip tarafından reddedildi

- **Tür:** DEFECT (sahip reddi) · **Risk:** MEDIUM · **Sahip skill:** tezcan-field-ux-audit
- **Kapsam:** UX-SG-01 (kabuk), tüm masaüstü ekranları (≥992 px)
- **Kesin kanıt:**
  - Sahip ifadesi (2026-09-25): "Kullanıcı masaüstü sidebar daraltma/genişletme tasarımını açıkça reddetti. Dar ikon sütunu, kopuk daraltma butonu ve gereksiz boş alan."
  - Uygulama (uncommitted): `core/templates/base.html` (`[data-sidebar-toggle]`), `core/static/core/css/app.css` (`html[data-sidebar="collapsed"]`, 68 px ray), `core/static/core/js/app.js` (daraltma işleyicisi), `core/static/core/js/sidebar-state.js`.
  - Ekran görüntüleri (LAB-SIM): `.playwright-mcp/ui/after/stock-collapsed-1440.png`, `issue-collapsed-1440.png`, `home-collapsed-1024.png`.
- **Yeniden üretim:** Sandbox veya dev sunucusunda ≥992 px genişlikte giriş yap → üst çubuktaki menü düğmesine tıkla → kenar çubuğu 68 px ikon sütununa daralır, düğme üst çubukta kenar çubuğundan kopuk kalır, form ekranlarında sağda geniş boş alan oluşur.
- **Beklenen:** Sahibin kabul ettiği bir masaüstü gezinme düzeni.
- **Gerçekleşen:** Reddedilen dar ikon sütunu + kopuk daraltma düğmesi + gereksiz boş alan.
- **Önerilen çözüm:** Henüz yok. Önce `tezcan-field-ux-audit` A çalışması (dar UI-001 navigasyon incelemesi: UX-SG-01 masaüstü kabuğu, temsilî pano/liste/form ekranları) kanıtı bu kayda ekler; yeniden tasarım sahibin onayladığı yönle ayrı görevde yapılır. Bütün ekranların bağımsız UX denetimi (B) ayrı ve daraltılmamış bir koşudur; UI-001'i yeniden denetlemez, bu kayda atıf yapar (sahip talimatı 2026-09-26).
- **Bağımsız doğrulama:** NOT_STARTED
- **Kullanıcı kabul durumu:** OWNER_REJECTED (2026-09-25)
- **Durum:** OPEN
- **İlgili:** `docs/UI-POLISH-STATUS.md` "Son canlı UI düzeltme geçişi" (UI-001 orada uygulanmış olarak anlatılıyor; kabul edilmiş sayılmamalı)

## BE-001 — Geliştirme DB'sinde ledger geçmişi olmayan dört `StockBalance`

- **Tür:** DEFECT (veri) · **Risk:** HIGH (projeksiyon doğrulaması kırmızı; pilot öncesi temiz olmalı) · **Sahip skill:** tezcan-backend-integrity
- **Kapsam:** BE-T-12; yalnız development DB
- **Kesin kanıt (LAB-DEV):** `.venv\Scripts\python.exe manage.py verify_inventory_projection` → "Quantity projection drift detected: 4 mismatch(es)", `M-a5cd8540`, `M-5ad07b74`, `M-24a9f142`, `M-ef8a4838` için `expected=0.000; actual=5.000`. Salt okunur inceleme: `docs/DEV-DB-PROJECTION-DRIFT-REPAIR-PROPOSAL.md` (her kümede 0 ledger satırı, 0 `AuditEvent` referansı; 2026-09-12 21:47–21:48 UTC).
- **Yeniden üretim:** Yukarıdaki komut (salt okunur).
- **Beklenen:** `Inventory projection is consistent.` (`AGENTS.md` §9)
- **Gerçekleşen:** 4 sapma; kaynak depo dışı bir deneme betiği.
- **Önerilen çözüm:** Onarım önerisi belgesindeki seçenek A (yedek → dry-run → yetkili/denetimli sıfırlama → master pasifleştirme). **Denetçiler dokunmaz.** Minimum stok hesabı bu satırların kondisyonlarını (`CD-…`, sınıflandırılmamış) saymaz.
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** AWAITING_OWNER (onarım yolu seçimi) · **Durum:** OPEN
- **İlgili:** `DEC-042` (yerel, commit edilmemiş karar kaydı), ENV-001

## BE-002 — Kararsız sayım testi: onaylı mutabakat temelinin değişmezliği

- **Tür:** DEFECT (test kararlılığı) · **Risk:** MEDIUM (gerçek bir değişmezlik açığını maskeleyebilir ya da yanlış alarm üretir) · **Sahip skill:** tezcan-backend-integrity
- **Kapsam:** `counting/test_reconciliation.py::test_approved_snapshot_actor_time_bucket_and_link_are_frozen`; BE-T-07
- **Kesin kanıt (LAB-DEV, 2026-09-25):** Tam pytest turunda 1 kez `Failed: DID NOT RAISE DatabaseError`. Aynı test tek başına 3/3 ve modülüyle 30/30 geçti. `counting/migrations/0003…py:86` ve `0004…py:230` guard'ı yalnız `NEW.counted_at IS DISTINCT FROM OLD.counted_at` olduğunda tetikleniyor; test `counted_at: timezone.now()` ile güncelliyor.
- **Yeniden üretim:** Tam paket tekrar koşuları (izole test DB'de); Windows saat çözünürlüğü (~15,6 ms) şüpheli.
- **Beklenen:** Deterministik olarak `DatabaseError`.
- **Gerçekleşen:** Nadiren hata yok (muhtemelen yeni değer eskisiyle aynı zaman damgası).
- **Önerilen çözüm:** Testte kesinlikle farklı bir `counted_at` (ör. `+1 gün`) kullanmak; guard'a dokunmadan. Kök neden doğrulanmadı (hipotez).
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** N/A · **Durum:** OPEN
- **İlgili:** Benzer kök neden daha önce `inventory/services/expiry.py`'de düzeltildi (uncommitted).

## ENV-001 — Ayrılmış izole test veritabanı yok; sandbox ile test DB'si paylaşılıyor

- **Tür:** ENV · **Risk:** HIGH (test sonuçları ve sandbox verisi birbirini bozar) · **Sahip skill:** tezcan-backend-integrity (tüm skill'leri etkiler)
- **Kapsam:** Tüm DB gerektiren test/denetim adımları
- **Kesin kanıt (LAB-DEV, 2026-09-25):** `POSTGRES_TEST_DB=test_tezcan_envanter_ui pytest --create-db` → `permission denied to create database`. Sandbox (`:8001`, `pilot_settings`) `test_tezcan_envanter` kullanıyor; sandbox açıkken odaklı testlerde 12 sayım iddiası başarısız oldu (ör. `assert 24 == 0`), sandbox kapatılıp DB sıfırlanınca 1331/1331 geçti.
- **Yeniden üretim:** Sandbox açıkken `pytest catalog/tests/test_inbound_material_foundation.py`.
- **Beklenen:** Denetim testleri kendi DB'sinde, sandbox'tan bağımsız.
- **Gerçekleşen:** Tek test DB'si; rolün `CREATEDB` yetkisi yok.
- **Önerilen çözüm:** Sahip/IT `test_tezcan_envanter_audit` DB'sini (aynı rol sahipliğinde, migration'lar pytest ile) oluşturur; denetim testleri `POSTGRES_TEST_DB=test_tezcan_envanter_audit` ile koşar; `test_db_guard.py` her koşudan önce çalışır.
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** AWAITING_OWNER · **Durum:** OPEN

## ENV-002 — Tam test turundan sonra kanonik kondisyon seed'i kayboluyor

- **Tür:** ENV · **Risk:** LOW · **Sahip skill:** tezcan-backend-integrity
- **Kesin kanıt (LAB-DEV, 2026-09-25):** Tam odaklı tur sonrası `catalog/tests/test_material_condition.py` 6 test `MaterialCondition.DoesNotExist`; `catalog.0004` seed fonksiyonu yeniden çalıştırılınca geçti. Önceki durum belgelerinde de kayıtlı ("test-DB MaterialCondition seed kaybı").
- **Beklenen:** Test sırası/tablo boşaltan testlerden bağımsız sonuç.
- **Önerilen çözüm:** Kök nedeni (tablo boşaltan TransactionTestCase/flush) bul; test fixture'ı ile seed'i garanti et. Doğrulanmadı.
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** N/A · **Durum:** OPEN

## DOC-001 — Ekran envanteri yeni ekranları içermiyor

- **Tür:** DOC · **Risk:** LOW · **Sahip skill:** tezcan-field-ux-audit
- **Kesin kanıt:** `docs/UI-POLISH-MASTER-PLAN.md` §2'de 29 grup + Admin var; `/inventory/stock/minimum/` (Minimum stok altındakiler) ve HTMX göz içeriği/stok kaynakları parçaları yok.
- **Önerilen çözüm:** Envantere eklenmesi (ayrı belge görevi). Senaryo matrisinde geçici olarak UX-SG-31/32.
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** N/A · **Durum:** OPEN

## UX-001 — Mal Kabul'de "Kabul konumu" alanının altında eski "Hedef konum" hata metni görünebilir

- **Tür:** DEFECT (terminoloji; tarayıcıda doğrulanmadı) · **Risk:** LOW · **Sahip skill:** tezcan-field-ux-audit
- **Kesin kanıt (kod):** `inventory/services/receipts.py` "Hedef konum aktif ve stok tutabilir olmalıdır." mesajı; form alan etiketi `inventory/forms.py` `RECEIPT_LOCATION_LABEL = "Kabul konumu"`.
- **Yeniden üretim (planlı):** Mal Kabul'de pasif veya stok tutmayan konumla POST (form listesi bunu engelliyor; servis hatası ancak yarış durumunda görünür).
- **Önerilen çözüm:** Form katmanında hata kodunu alan etiketine uygun metne çevirmek (servis metni değişmeden). Önce tarayıcıda yeniden üretilmeli.
- **Bağımsız doğrulama:** NOT_STARTED · **Kullanıcı kabul durumu:** N/A · **Durum:** OPEN
