# Senaryo ve Kabul Kriteri Matrisi

- **Tek kaynak:** Denetim kabul kriterleri yalnız burada tutulur. Skill'ler ve raporlar kimlikle (ör. `UX-SG-13`, `BE-T-02`) atıf yapar.
- **Durum sütunu** bu denetim programının hükmüdür. Önceki geliştirme turlarında görülen sonuçlar "Önceki kanıt" sütununda ayrıca belirtilir; yeniden doğrulanmadıkça PASS sayılmaz.
- **Başlangıç:** 2026-09-25. Hiçbir satır bu program içinde denetlenmedi; tüm hükümler `PENDING`.
- **Kod tabanı durumu:** Bu belgeler 2026-09-25/26'da HEAD `26496d9` + **commit edilmemiş yerel çalışma ağacı** üzerinde yazıldı. `(yerel)` işaretli özellikler (minimum stok / `DEC-042`, TZ1L göz okutma ve göz içeriği parçaları, tek sayfa Mal Kabul, UI-001 kenar çubuğu daraltma, SKT kontrol sırası düzeltmesi) yalnız yerel çalışma ağacında vardır; commit edilmiş, yayımlanmış veya kabul edilmiş özellik değildir. "Önceki kanıt" sütunundaki pilot (S1–S12) ve sandbox sonuçları bu yerel koda aittir. Gerçek koşu, hangi kodu denetlediğini (HEAD / yerel) raporda ayırır.
- **Ortam etiketleri:** `LAB-SIM` (Playwright/simüle), `LAB-DEV` (dev sunucusu), `DEVICE` (gerçek okuyucu/yazıcı/telefon), `PROD`.

## 1. Ürün kabul kriterleri (`docs/00-PRODUCT.md` ACC-001..013)

| Kimlik | Kriter (özet) | Sahip skill | Önceki kanıt | Durum |
|---|---|---|---|---|
| ACC-001 | Kimlik doğrulama + role uygun erişim | security | Birim testler mevcut (kapsam doğrulanmadı) | PENDING |
| ACC-002 | Adetli ve tekil malzeme tanımlanıp izlenebilir | backend | Birim testler; TZ1L pilotu S7 (LAB-SIM) | PENDING |
| ACC-003 | Malzeme hiyerarşik konum/rafta bulunabilir | field-ux | TZ1L pilotu S1–S11 (LAB-SIM) | PENDING |
| ACC-004 | Giriş/çıkış/iade/transfer stok ve geçmişe doğru yansır | backend | Birim testler; pilot S1, S5, S6 (LAB-SIM) | PENDING |
| ACC-005 | Çıkış alıcı adı/soyadı/sicil, hat, kullanım yeri, sistem zamanı olmadan tamamlanmaz | backend + field-ux | Birim testler | PENDING |
| ACC-006 | Negatif stok reddedilir | backend | Birim testler | PENDING |
| ACC-007 | Geçmiş sessiz değiştirilemez; fotoğraflı düzeltme talebi ve onay | backend + security | Birim testler (`DEC-030`, `DEC-034`) | PENDING |
| ACC-008 | Minimum stok, haftalık giriş/çıkış, kullanım, en çok azalan görünürlüğü | field-ux + backend | Minimum stok `DEC-042` (yerel, commit edilmemiş; 9 test). Haftalık/kullanım/azalan raporları **yok** (`DEC-OPEN-014`) | PENDING — kısmen uygulanmamış |
| ACC-009 | Excel içe/dışa aktarım + fiziksel sayım/mutabakat | backend | Sayım/baseline testleri. Excel import UI yok (`DEC-OPEN-015`) | PENDING — kısmen uygulanmamış |
| ACC-010 | Malzeme/konum QR/barkod desteği canlıdan önce doğrulanır | field-ux | Yalnız LAB-SIM (pilot). DEVICE testi yapılmadı | PENDING |
| ACC-011 | Önemli işlemler kullanıcı + sistem zamanıyla denetlenebilir | backend + security | Birim testler | PENDING |
| ACC-012 | Fiziksel mutabakat onaylanmadan sistem stoğu yetkili sayılmaz | backend | Baseline testleri (`DEC-033`) | PENDING |
| ACC-013 | Yedekleme + geri yükleme hazırlığı kabul senaryosuyla gösterilir | security | Yok. Restore drill yapılmadı (`05-ARCHITECTURE` §30) | PENDING — eksik |

## 2. Zorunlu test senaryoları (`AGENTS.md` §20, §11)

"Anahtar kelime eşleşmesi" yalnız depoda ilgili test dosyası bulunduğunu gösterir; kapsamın yeterliliği `tezcan-backend-integrity` tarafından doğrulanacaktır. Tüm eşzamanlılık testleri gerçek PostgreSQL gerektirir.

| Kimlik | Senaryo | Sahip | Önceki kanıt | Durum |
|---|---|---|---|---|
| BE-T-01 | Negatif stok reddi | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-02 | Eşzamanlı son stok çıkışı | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-03 | Idempotency: aynı `operation_id` aynı/farklı fingerprint | backend | Anahtar kelime eşleşmesi var; tekil kabul tekrar gönderimi testi (2026-09-25) | PENDING |
| BE-T-04 | Tekil varlık tek konumda; eşzamanlı tekil hareket | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-05 | Yetkisiz mal kabul | security | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-06 | Değişmez düzeltme geçmişi ve çift onay | backend | 2 dosyada "double approval" | PENDING |
| BE-T-07 | Sayım stoğu sessizce değiştirmez; mutabakat çift commit | backend | Sayım testleri | PENDING |
| BE-T-08 | Import mutabakatı atlayamaz; import commit sıfır ledger/bakiye | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-09 | Pasif konum reddi | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-10 | Eşzamanlı ilk `StockBalance` satırı | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-11 | Ledger DB değişmezlik guard'ı: bulk update/delete | backend | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-12 | Ledger ↔ projeksiyon tutarlılığı (`verify_inventory_projection`) | backend | Dev DB'de 4 sapma raporlanıyor (BE-001) | PENDING |
| BE-T-13 | Sayım oturumu sırasında hareket yarışı (drift) | backend | Sayım testleri | PENDING |
| BE-T-14 | Baseline tekrar/idempotency, çift kuruluş | backend | 4 dosya eşleşmesi | PENDING |
| BE-T-15 | Bozuk/aşırı büyük workbook, formül enjeksiyonu | security | Anahtar kelime eşleşmesi var | PENDING |
| BE-T-16 | Beklenmeyen deadlock/transaction hatasında tam rollback | backend | **Bulunamadı** (açık eksik) | PENDING |

## 3. Ekran grupları (`docs/UI-POLISH-MASTER-PLAN.md` §2, 29 grup + Admin)

Rota, şablon ve izin ayrıntısı master plandadır; burada tekrarlanmaz. Önceki UI programı tüm grupları "DONE" saydı; bu bir tasarım uygulama durumudur, saha kabulü değildir.

| Kimlik | Ekran grubu | Günlük işlem | Durum |
|---|---|---|---|
| UX-SG-01 | Kabuk (menü, üst çubuk, alt gezinme) | Tümü | **FAIL (sahip reddi)** — UI-001 |
| UX-SG-02 | Giriş | — | PENDING |
| UX-SG-03 | Ana sayfa (okut/ara, dört işlem, Takip) | Tümü | PENDING |
| UX-SG-04 | 403/404/500 | — | PENDING |
| UX-SG-05 | Mevcut Stok | Mevcut Stok | PENDING |
| UX-SG-06 | Mal Kabul (tek sayfa, HTMX özet) (yerel) | Mal Kabul | PENDING |
| UX-SG-07 | Malzeme listesi | — | PENDING |
| UX-SG-08 | Malzeme detayı | — | PENDING |
| UX-SG-09 | Malzeme formu + model okutma | — | PENDING |
| UX-SG-10 | Mal kabul kaydı detayı | Mal Kabul | PENDING |
| UX-SG-11 | Tekil mal kabul (POST; GET tek sayfaya yönlenir) (yerel) | Mal Kabul | PENDING |
| UX-SG-12 | Transfer formu/detayı | Yerleştir / Transfer | PENDING |
| UX-SG-13 | Çıkış formu/detayı | Stok Çıkışı | PENDING |
| UX-SG-14 | İade formu/detayı | — | PENDING |
| UX-SG-15 | Tekil varlık detayı + çıkış/iade/transfer | Stok Çıkışı, Transfer | PENDING |
| UX-SG-16 | Talep Takip | Mal Kabul | PENDING |
| UX-SG-17 | SKT uyarıları ve kontrol | — | PENDING |
| UX-SG-18 | Envanter hareketleri | — | PENDING |
| UX-SG-19 | Düzeltme talepleri + kanıt | — | PENDING |
| UX-SG-20 | Tarama sayfası (USB HID + kamera) | Tümü | PENDING |
| UX-SG-21 | Etiketler (Code128/QR × M/A/L) | — | PENDING |
| UX-SG-22 | Fiziksel sayım | — | PENDING |
| UX-SG-23 | Kesim (baseline) | — | PENDING |
| UX-SG-24 | Yönetim merkezi | — | PENDING |
| UX-SG-25 | Kategori, ölçü birimi | — | PENDING |
| UX-SG-26 | Konumlar (detayda göz içeriği + satır Çıkış/Transfer) (yerel) | Stok Çıkışı, Transfer | PENDING |
| UX-SG-27 | Çalışanlar | — | PENDING |
| UX-SG-28 | Üretim hatları | — | PENDING |
| UX-SG-29 | Roller, izinler, kullanıcılar | — | PENDING |
| UX-SG-30 | Django Admin | — | N/A (kilitli koruma yüzeyi; güvenlik skill'i yalnız erişim kısıtını denetler) |
| UX-SG-31 | **Envanter dışı:** Minimum stok altındakiler (`/inventory/stock/minimum/`) (yerel) | Mevcut Stok | PENDING — master plan envanterine eklenmemiş (DOC-001) |
| UX-SG-32 | **Envanter dışı:** Göz içeriği ve stok kaynakları parçaları (HTMX) (yerel) | Çıkış, Transfer | PENDING — master plan envanterine eklenmemiş (DOC-001) |

## 4. Günlük akışlar (uçtan uca)

| Kimlik | Akış | Önceki kanıt | Durum |
|---|---|---|---|
| UX-F-01 | Okut → Mevcut Stok / konum / malzeme bulma | Pilot S11 (LAB-SIM) | PENDING |
| UX-F-02 | Mal Kabul: malzeme seç/okut → adetli önizleme/onay ya da tekil kayıt → etiket | Birim testler | PENDING |
| UX-F-03 | Stok Çıkışı: göz (TZ1L) okut (yerel) → içerik → miktar → alıcı/hat/yer → kaydet | Pilot S1–S4, S8–S10 (LAB-SIM) | PENDING |
| UX-F-04 | Yerleştir/Transfer: kaynak göz → hedef göz → miktar | Pilot S5–S7 (LAB-SIM) | PENDING |
| UX-F-05 | Boşalan gözün yeni ürünle yeniden kullanımı | Pilot S6 (LAB-SIM) | PENDING |
| UX-F-06 | Yanlış okutma (TZ1M/TZ1A/EAN/bilinmeyen TZ1L) | Pilot S9 (LAB-SIM) | PENDING |
| UX-F-07 | Minimum stok uyarısından Mal Kabul'e (yerel) | Sandbox kontrolü (LAB-SIM) | PENDING |
| UX-F-08 | Sayım oturumu: başlat → kör sayım → tamamla → fark onayı | — | PENDING |

## 5. Fiziksel cihaz senaryoları (`DEC-038`, QR-004, ACC-010)

| Kimlik | Senaryo | Önceki kanıt | Durum |
|---|---|---|---|
| DEV-01 | Kodscan KDS-5040 ile TZ1M/TZ1L/TZ1A Code128 okuma (USB HID) | Yok | PENDING (KDS-5040 mevcut; fiziksel doğrulama yapılmadı) |
| DEV-02 | Okuyucu Enter'ı formu göndermez; alan geçişi | LAB-SIM geçti | PENDING (KDS-5040 mevcut; DEVICE doğrulaması yapılmadı) |
| DEV-03 | Kodprint DT-482 203 DPI, 104 mm etiket okunabilirliği | Yok | PENDING (DT-482 mevcut; fiziksel doğrulama yapılmadı) |
| DEV-04 | Telefon kamerası (ZXing) Code128 + QR | Yok | PENDING (cihaz gerekli) |
| DEV-05 | Raf gözüne yapıştırılmış etiketin mesafe/açı ile okunması | Yok | PENDING (DT-482 + KDS-5040 mevcut; fiziksel doğrulama yapılmadı) |

## 6. Güvenlik ve kurtarma (`05-ARCHITECTURE` §29–30, `AGENTS.md` §18–19, §24)

| Kimlik | Senaryo | Önceki kanıt | Durum |
|---|---|---|---|
| SEC-01 | Rol bazlı sunucu tarafı yetki: her POST/HTMX uç noktası | Birim testler (kapsam doğrulanmadı) | PENDING |
| SEC-02 | Nesne düzeyi erişim / IDOR: düzeltme, kanıt, sayım, tekil varlık, etiket görüntüleri | Kanıt dosyası uç noktası testleri | PENDING |
| SEC-03 | CSRF tüm durum değiştiren istekler (HTMX dahil) | — | PENDING |
| SEC-04 | Oturum/cookie güvenli ayarları; `check --deploy` | — | PENDING |
| SEC-05 | Git'te sır yok; ortam değişkeni yönetimi | Anlık görüntü: projede `.env` yok, POSTGRES_* kullanıcı ortamında | PENDING |
| SEC-06 | Kanıt/medya: private storage, public `MEDIA_URL` yolu yok | `DEC-034` testleri | PENDING |
| SEC-07 | PostgreSQL rol yetkileri (en az yetki; uygulama rolü DDL/trigger devre dışı bırakamaz) | Bilinen: rolün CREATEDB yetkisi yok | PENDING |
| SEC-08 | Tarama/çözümleme: kimlik doğrulamasız erişim ve sayım (enumeration) yok | Birim testler, pilot S12 | PENDING |
| SEC-09 | Excel export formül nötralizasyonu | Anahtar kelime eşleşmesi | PENDING |
| SEC-10 | Tutarlı PostgreSQL yedeği → medya yedeği sırası | Yok | PENDING |
| SEC-11 | İzole ortamda restore drill: ledger bütünlüğü, projeksiyon, ek→dosya varlığı, orphan raporu | Yok | PENDING (izole ortam gerekli) |
| SEC-12 | Üretim restore denemesi | — | Kapsam dışı (ayrı yazılı onay) |

## 7. Performans senaryoları

Ölçüm protokolü ve sonuçlar `PERFORMANCE-BASELINE.md`'dedir.

| Kimlik | Etkileşim | Durum |
|---|---|---|
| PERF-01 | Ana sayfa ilk yükleme (giriş sonrası) | PENDING |
| PERF-02 | TZ1L okut → göz içeriği görünür (HTMX) | PENDING |
| PERF-03 | Malzeme seçimi → stok kaynakları (HTMX) | PENDING |
| PERF-04 | Çıkış kaydı POST → detay sayfası | PENDING |
| PERF-05 | Mevcut Stok listesi (arama + sayfalama) | PENDING |
| PERF-06 | Minimum stok listesi (malzeme düzeyi alt sorgular) | PENDING |
| PERF-07 | Malzeme detayı (bakiye + son hareketler + minimum durum) | PENDING |
| PERF-08 | Hareket geçmişi listesi | PENDING |
