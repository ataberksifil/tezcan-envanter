---
name: tezcan-field-ux-audit
description: Tezcan Envanter saha kullanılabilirlik denetimi. Depo çalışanının günlük dört işlemi (Mevcut Stok, Mal Kabul, Stok Çıkışı, Yerleştir/Transfer), 29 ekran grubu, TZ1M/TZ1L/TZ1A okutma, USB HID klavye-okuyucu davranışı, mobil ve erişilebilirlik kanıtla denetlenir; bulgular docs/audits kayıtlarına yazılır. Yalnız kullanıcı açıkça bir UX/saha denetimi istediğinde kullanılır; tasarım değiştirmez, kod yazmaz.
disable-model-invocation: true
argument-hint: "[DRY_RUN] [kapsam: gunluk-dort | UX-SG-xx..yy | UX-F-xx | UI-001]"
---

# Tezcan saha UX denetimi

Amaç: bir depo çalışanının gerçek işini (okut → bul → kaydet) bu arayüzle **hatasız, az adımla ve güvenle** bitirip bitiremediğini kanıtla göstermek. Güzellik yargısı değil, iş başarısı ve hata önleme ölçülür, çünkü bu üründe yanlış göz/yanlış malzeme seçimi doğrudan stok hatasına dönüşür (`AGENTS.md` §3).

## Önce oku

1. `AGENTS.md` (§3 ürün önceliği, §13 ISSUE verisi, §18 QR/kimlik) ve `docs/audits/AUDIT-RUNBOOK.md` (ortak yasaklar, kanıt, hüküm).
2. `docs/audits/SCENARIO-MATRIX.md` §3–5: ekran grupları UX-SG-01…32, akışlar UX-F-01…08, cihaz senaryoları DEV-01…05. Kriterleri buradan al; yeni kriter uydurma.
3. `docs/audits/FINDINGS-REGISTER.md`: açık bulgular (özellikle **UI-001 OWNER_REJECTED**).
4. Gerekince: `references/field-checklist.md` (kontrol listesi), `references/plugin-division.md` (tasarım eklentileriyle iş bölümü).

## Kapsam sınırı

- **Sahip:** görev tamamlama, adım/okutma/seçim sayısı, hata mesajları ve kurtarma, TZ1 anlamlarının doğru sunulması, HID Enter davranışı, 390–1440 px düzen, klavye/odak/ekran okuyucu, Türkçe metin tutarlılığı.
- **Sahip değil:** milisaniye ölçümü → `tezcan-interaction-performance`; stok sonucunun doğruluğu → `tezcan-backend-integrity`; yetkisiz erişim → `tezcan-stock-data-security-audit`; kod yapısı → `tezcan-architecture-review`. Bu alanlarda şüphe görürsen bulgu açma, "İlgili" notu ile sahibine yönlendir.
- Başka skill'in alanındaki şüpheyi bulgu olarak açma; raporun "Yönlendirme" bölümüne sahibini yaz ve `FINDINGS-REGISTER.md`'de aynı kapsam varsa yeni kayıt açma (runbook §1 çakışma önleme).

## DRY_RUN

Çağrı `DRY_RUN` ile başlarsa gerçek denetim yapılmaz; `docs/audits/AUDIT-RUNBOOK.md` §1a protokolü ve şablonu uygulanır. Yalnız talimat ve kaynak dosyaları okunur, varlıkları kontrol edilir; betik, git, DB, pytest, tarayıcı veya HTTP yok; hiçbir kayıt güncellenmez; önkoşullar `NOT_CHECKED (DRY_RUN)`.

- **Yüklenecek talimatlar:** `references/field-checklist.md`, `references/plugin-division.md`; SCENARIO-MATRIX §3–5; FINDINGS-REGISTER UI-001, UX-001, DOC-001.
- **Gerçek koşuda kullanılacak kaynaklar:** Playwright MCP (sandbox `http://localhost:8001`), rol başına sentetik oturum, viewport 1440/1280/1024/768/390, ekran görüntüsü klasörü `.playwright-mcp/audits/field-ux/`; isteğe bağlı `impeccable` critique.
- **Bilinen engeller:** fiziksel cihaz doğrulaması yapılmadı (KDS-5040 okuyucu ve DT-482 yazıcı mevcut; DEV-01…03, DEV-05 PENDING; telefon kamerası DEV-04 için cihaz durumu kayıtlı değil), sandbox kapalı, durum değiştiren adım için sahip onayı yok.

## İki ayrı çalışma: UI-001 incelemesi ve bütün-ekran denetimi

**A. UI-001 navigasyon incelemesi** (dar; `UI-001` kapsamıyla çağrılır)

- Yalnız UX-SG-01 masaüstü kabuk gezinmesi (≥992 px): kenar çubuğunun açık/daraltılmış hali, 68 px ikon rayı, daraltma düğmesinin yeri ve kenar çubuğuyla ilişkisi, içerikte oluşan boş alan, klavye/`aria-expanded`/erişilebilir adlar, tercih kalıcılığı; <992 px'te kaydırmalı menüde gerileme olmadığı.
- Temsilî ekranlar: ana sayfa (pano), Mevcut Stok (liste), Stok Çıkışı (form). 1440/1280/1024 px açık + daraltılmış; 768/390 px yalnız menü gerilemesi. Bu ekranların kendi UX kriterleri burada değerlendirilmez.
- Çıktı: kanıt yalnız mevcut UI-001 kaydına eklenir, yeni bulgu açılmaz; UX-SG-01 FAIL (sahip reddi) kalır; gerekirse sahip onayına PROPOSAL. Bu inceleme bütün-ekran denetiminin yerine geçmez.

**B. Bütün ekranların bağımsız UX denetimi** (sonraki ayrı koşu)

- Kapsam daraltılmaz: UX-SG-01…32 (UX-SG-30 Admin N/A), UX-F-01…08, DEV-01…05 ve `references/field-checklist.md` A–E'nin tamamı. UX-SG-01'in UI-001 dışındaki yönleri (üst çubuk, okut/ara, mobil alt gezinme, menüye erişim) dahildir.
- UI-001'in konusu (masaüstü daraltma tasarımı) yeniden denetlenmez ve bunun için yeni kayıt açılmaz; raporda "UI-001'e bağlı — bkz. FINDINGS-REGISTER" diye atıf yapılır. Bu konuda rastlanan yeni kanıt yalnız UI-001 kaydına eklenir.
- İstisna: UI-001 için yeniden tasarım uygulandığında onun doğrulaması UI-001'in bağımsız doğrulamasıdır (kapanış kuralı); bir kez ve UI-001 kaydında yapılır, yeni denetim sayılmaz.

## Önkoşullar (her biri PASS değilse ilgili adım PENDING)

- Git HEAD ve çalışma ağacı durumu kaydedildi (`git status --porcelain | wc -l`).
- Tarayıcı kanıtı yalnız sandbox'ta (`http://localhost:8001`, izole test DB) veya sahibin verdiği oturumla alınır. Dev sunucusunda (`:8000`) yalnız okuma/gezinme; kayıt oluşturan form gönderilmez.
- Durum değiştiren adım (ör. çıkış kaydet) yalnız sandbox'ta ve sahip bunu kapsamda onayladıysa.
- Fiziksel cihaz senaryoları (DEV-xx) cihaz ve kullanıcıyla fiziksel olarak doğrulanana kadar PENDING. KDS-5040 ve DT-482 mevcuttur fakat doğrulanmamıştır; cihazın var olması PASS değildir, `LAB-SIM` (payload + Enter) cihaz kanıtının yerine geçmez.

## Yasaklar

Runbook §3'e ek olarak: şablon/CSS/JS değiştirmek, "hızlı düzeltme" yapmak, `impeccable` tasarım modlarını (polish, layout, bolder…) çalıştırmak, sahibin tarayıcıdaki kişisel sekmelerine dokunmak, UI-001'i kabul edilmiş veya kapanmış göstermek.

## Yöntem

1. **Kapsamı sabitle.** Çağrıdaki kapsamı SCENARIO-MATRIX kimliklerine çevir ve çalışmanın A (UI-001) mı B (bütün-ekran/akış) mı olduğunu yaz; kapsamsız çağrıda önce "günlük dört işlem" öner ve onay bekle. UI-001 yalnız açıkça istenirse A olarak koşulur.
2. **Rol başına çalış.** Her akışı en az STOREKEEPER ve TECHNICIAN izin setiyle dene; izin yoksa görünmeyen eylemler de kanıttır.
3. **Görev yürüyüşü.** Her akış için adımları, okutma/seçim/yazma sayılarını ve her ekranda karar noktalarını kaydet. HID okutması `LAB-SIM` olarak etiketlenir (payload + Enter).
4. **Viewport taraması.** 1440/1280/1024/768/390 px: yatay taşma (`scrollWidth - innerWidth > 0`), 44 px dokunma hedefi, sabit çubukların içeriği örtmesi, kaydet düğmesine erişim.
5. **Hata yolları.** Yanlış etiket türü, bilinmeyen kod, pasif malzeme/konum, yetkisiz okutma, çift gönderim, geri tuşu.
6. **Erişilebilirlik.** Tab sırası, görünür odak, etiket–alan ilişkisi, `aria-live` mesajları, renk dışı ipuçları.
7. **Sınıflandır.** Kanıtlı hata → DEFECT; iyileştirme → PROPOSAL (kullanıcı etkisi gerekçesiyle). Görsel yorum için `impeccable` critique çıktısı kullanıldıysa kaynak olarak belirt.
8. **Kaydet.** Bulguları `FINDINGS-REGISTER.md`'ye (UX-/UI- önekleri), hükümleri raporun senaryo tablosuna yaz. SCENARIO-MATRIX'teki "Durum" sütununu yalnız kanıtlı hükümle güncelle.

## Kanıt formatı

`[UX-SG-13 | STOREKEEPER | 390px | LAB-SIM] adım → gözlem → ekran görüntüsü .playwright-mcp/audits/field-ux/<tarih>/<ad>.png`

## Hüküm

- **PASS:** akış tamamlandı, kriter karşılandı, kanıt yolu var.
- **FAIL:** kriter ihlali tekrar üretildi; bulgu kimliği yazıldı. Sahibin reddettiği tasarım (UI-001) FAIL'dir ve sahip kabul etmeden PASS olamaz.
- **PENDING:** cihaz, rol, veri veya onay eksik; neyin eksik olduğu yazılır.

## Rapor

Runbook §8 şablonunu kullan. Ek olarak her akış için tek satır: `UX-F-xx | okutma n | seçim n | yazma n | hata yolu sonucu | hüküm`.

## Kapsam dışı

Yeniden tasarım, bileşen kütüphanesi değişikliği, performans bütçesi, backend doğrulaması, güvenlik testi, Django Admin görsel denetimi.
