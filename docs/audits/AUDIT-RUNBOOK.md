# Denetim Runbook'u — Tezcan Envanter

- **Durum:** Altyapı hazır; hiçbir kapsamlı denetim başlatılmadı (2026-09-25).
- **Kod tabanı durumu:** Bu belgeler 2026-09-25/26'da HEAD `26496d9` + **commit edilmemiş yerel çalışma ağacı** üzerinde yazıldı. `(yerel)` işaretli özellikler (minimum stok / `DEC-042`, TZ1L göz okutma ve göz içeriği parçaları, tek sayfa Mal Kabul, UI-001 kenar çubuğu daraltma, SKT kontrol sırası düzeltmesi) yalnız yerel çalışma ağacında vardır; commit edilmiş, yayımlanmış veya kabul edilmiş özellik değildir. "Önceki kanıt" sütunundaki pilot (S1–S12) ve sandbox sonuçları bu yerel koda aittir. Gerçek koşu, hangi kodu denetlediğini (HEAD / yerel) raporda ayırır.
- **Otorite:** `AGENTS.md` ve `docs/00`–`06`. Bu runbook onları değiştirmez; çelişki görülürse denetim durur ve kural kimliğiyle raporlanır.
- **Ortak kayıtlar:** kabul kriterleri ve senaryolar `SCENARIO-MATRIX.md`, bulgular `FINDINGS-REGISTER.md`, ölçüm tabanı `PERFORMANCE-BASELINE.md`. Başka yerde aynı bilgiyi tekrar etmeyin.

## 1. Beş denetim skill'i ve sınırları

| Skill | Sahip olduğu soru | Sahip olmadığı |
|---|---|---|
| `tezcan-field-ux-audit` | Saha çalışanı günlük işi (okut, kabul, çıkış, transfer, sayım) hatasız ve hızlı bitirebiliyor mu? 29 ekran grubu, TZ1M/TZ1L/TZ1A, USB HID, mobil, erişilebilirlik | Süre ölçümü (performans), stok doğruluğu (backend), yetki ihlali (güvenlik) |
| `tezcan-interaction-performance` | Etkileşimler ölçülebilir olarak ne kadar sürüyor, darboğaz nerede? p50/p95, trace, sorgu sayısı, PostgreSQL planı | Görsel tasarım yargısı, stok kuralı doğruluğu |
| `tezcan-backend-integrity` | Ledger, projeksiyon, negatif stok, eşzamanlılık, idempotency, sayım/düzeltme/baseline kuralları ve DB guard'ları kanıtla doğru mu? | Rol/nesne erişim saldırı yüzeyi (güvenlik), modül yapısı (mimari) |
| `tezcan-architecture-review` | Modül sınırları, bağımlılık yönü, kanıtlanmış teknik borç ve gereksiz karmaşıklık | Davranış doğruluğu (backend), UX, güvenlik açıkları |
| `tezcan-stock-data-security-audit` | Kim neyi görebilir/değiştirebilir? IDOR, CSRF, session, sırlar, kanıt/medya erişimi, PostgreSQL yetkileri, dağıtım ayarları, DB+media+config kurtarma doğrulaması | Stok matematiği (backend), performans |

Bir bulgu birden çok alana dokunuyorsa **tek** kayıt açılır; sahip skill kaydı açar, diğerleri "İlgili" alanına kimliğini yazar.

**Çakışma önleme (her skill için):**

1. Bir soru başka skill'in "Sahip" alanındaysa bulgu açılmaz. Raporun "Yönlendirme" bölümüne `→ <skill-adı>: <kısa gözlem>` yazılır.
2. Bulgu açmadan önce `FINDINGS-REGISTER.md`'de aynı kapsam/kimlik aranır. Kayıt varsa yenisi açılmaz; yalnız yeni kanıt "İlgili" veya kanıt alanına eklenir.
3. Aynı SCENARIO-MATRIX satırının "Sahip skill" sütununda adı olmayan skill o satıra hüküm yazmaz.
4. Bir çağrıda yalnız çağrılan skill çalışır; başka skill kendiliğinden başlatılmaz.

## 1a. DRY_RUN modu (tüm skill'ler)

Çağrı `DRY_RUN` sözcüğüyle başlarsa (ör. `/tezcan-backend-integrity DRY_RUN BE-T-02`) skill **gerçek denetim yapmaz**. Amaç, gerçek koşu öncesinde neyin yükleneceğini ve hangi sınırların uygulanacağını görmektir.

- **İzinli:** skill'in kendi `SKILL.md` ve `references/` dosyalarını, bu runbook'u, `SCENARIO-MATRIX.md` ve `FINDINGS-REGISTER.md`'yi okumak. Adı geçen dosya ve betiklerin **varlığını** kontrol etmek (liste/stat).
- **Yasak:** betik çalıştırmak (guard ve import taraması dahil), git komutu, veritabanı bağlantısı, pytest, tarayıcı veya MCP aracı, HTTP isteği, dosya yazmak, `FINDINGS-REGISTER`/`SCENARIO-MATRIX`/`PERFORMANCE-BASELINE`'ı güncellemek, bulgu veya hüküm üretmek.
- **Önkoşullar** kontrol edilmez; her biri `NOT_CHECKED (DRY_RUN)` olarak listelenir.

DRY_RUN raporu şu şablonu kullanır:

```
# <skill> DRY_RUN — <istenen kapsam> — <tarih>
Mod: DRY_RUN (denetim yapılmadı, hiçbir kayıt değişmedi)
Yüklenen talimatlar: <okunan dosyalar>
Kapsam eşlemesi: <istenen kapsam → SCENARIO-MATRIX kimlikleri; sahip skill'i başka olanlar → Yönlendirme>
Önkoşullar: <madde → NOT_CHECKED (DRY_RUN)>
Gerçek koşuda kullanılacak kaynaklar: <dosyalar, betikler, araçlar, sunucu/DB hedefleri, ortam etiketi>
Kaynak varlık kontrolü: <dosya/betik → VAR/YOK>
Güvenlik sınırları: <bu skill için geçerli yasaklar>
Gerçek koşuda izlenecek adımlar: <numaralı liste, çalıştırılmadı>
Gerçek koşuyu engelleyen durumlar: <ör. ENV-001, fiziksel cihaz doğrulaması yapılmadı, onay yok>
Yönlendirme: <başka skill'e ait sorular>
```

## 2. Çağırma kuralı

- Denetimler yalnız kullanıcı açıkça çağırınca başlar (`/tezcan-…` veya adıyla). Skill'lerin `disable-model-invocation: true` ayarı modelin kendiliğinden yüklemesini engeller.
- Çağrı bir kapsam içermelidir (ör. "günlük dört işlem", "ekran grubu 12–15", "Mal Kabul idempotency"). Kapsamsız "her şeyi denetle" isteğinde skill önce kapsamı ve süreyi önerir, onay bekler.
- Bir skill başka bir skill'i kendiliğinden başlatmaz; gerekiyorsa sonraki adım olarak önerir.

Skill'ler değiştirildiğinde statik doğrulama (salt okunur, model çalıştırmaz): `python .claude/audit-tools/validate_audit_skills.py` (çıkış 0 = tüm kontroller geçti).

## 3. Ortak yasaklar (her skill için geçerli)

1. Ürün kodu, şablon, statik dosya, migration veya ayar değiştirmek. Bulgu yalnız önerilen çözüm olarak yazılır.
2. Development DB'ye yazmak; ledger, `StockBalance`, `SerializedAsset` veya `AuditEvent` satırı oluşturmak/değiştirmek/silmek. Dev DB'de yalnız `SET TRANSACTION READ ONLY` altında okuma yapılabilir.
3. Bilinen dört ledger'sız dev bakiyesini (`FINDINGS-REGISTER` BE-001) onarmak veya "temizlemek".
4. Çalışan dev sunucusunu (`127.0.0.1:8000`) veya sandbox'ı (`localhost:8001`) durdurmak/yeniden başlatmak.
5. Commit, push, reset, revert, stash, branch silme.
6. Üretim veya pilot ortamında test/kurtarma denemesi (ayrı yazılı onay gerekir).
7. Sır, parola, oturum çerezi veya `.credentials` içeriğini rapora ya da bulguya yazmak. Kanıt gerekiyorsa yalnız değişken adı ve "mevcut/yok" bilgisi.
8. Yeni bağımlılık, eklenti veya MCP sunucusu kurmak (ayrı izin).

## 4. İzole test veritabanı

- Durum değiştiren her test yalnız **ayrılmış** PostgreSQL test DB'sinde koşar. Pytest `POSTGRES_TEST_DB` ile hedeflenir.
- Mevcut tek test DB'si `test_tezcan_envanter` sandbox (`:8001`, `pilot_settings`) tarafından da kullanılır. Sandbox açıkken bu DB'de pytest koşmak sonuçları bozar (gözlendi: sayım iddiaları `24 == 0`) ve sandbox verisini etkileyebilir.
- DB rolünün `CREATEDB` yetkisi yok; ayrılmış bir denetim test DB'si (öneri: `test_tezcan_envanter_audit`) sahip/IT tarafından oluşturulmalı. Oluşturulana kadar DB gerektiren test adımları **PENDING** kalır (bkz. `FINDINGS-REGISTER` ENV-001).
- `POSTGRES_TEST_DB` pytest ile **aynı PowerShell oturumunda** ayarlanır; guard aynı hedefle pytest'ten önce çalışır; FAIL verirse (çıkış ≠ 0) test koşulmaz. Guard, `POSTGRES_TEST_DB` boşsa veya `--db` değerinden farklıysa FAIL verir. Bash tarzı satır içi önek (`POSTGRES_TEST_DB=… pytest`) PowerShell'de geçersizdir.

```powershell
$env:POSTGRES_TEST_DB = '<hedef_test_db>'
.venv\Scripts\python.exe .claude\skills\tezcan-backend-integrity\scripts\test_db_guard.py --db $env:POSTGRES_TEST_DB
if ($LASTEXITCODE -eq 0) { .venv\Scripts\python.exe -m pytest <hedef testler> }
```

Guard salt okunurdur: süreç listesini, `POSTGRES_TEST_DB` değişkenini ve `pg_stat_activity`'yi okur, hiçbir şey yazmaz.

**Doğrulama durumu (2026-09-26): PENDING.** `POSTGRES_TEST_DB` eşleşme koşulu yalnız statik olarak (derleme + kod okuması) doğrulandı; guard gerçek bir PostgreSQL DB'sine karşı henüz çalıştırılmadı. İlk gerçek kullanımda ayrılmış denetim DB'siyle (ENV-001) PASS/FAIL davranışı kaydedilir; sandbox ve development DB'si bu doğrulama için kullanılmaz.

## 5. Kanıt standardı

Her kanıt yeniden üretilebilir olmalıdır:

- **Komut kanıtı:** tam komut, çalışma dizini, çıkış kodu, ilgili çıktı satırları (sırsız).
- **Tarayıcı kanıtı:** URL, kullanıcı rolü (izin listesi), viewport, adımlar, ekran görüntüsü yolu (`.playwright-mcp/audits/<skill>/<tarih>/`, git dışı).
- **DB kanıtı:** salt okunur sorgu metni, hedef DB adı, satır sayısı/özeti. Kişisel veri maskelenir.
- **Kod kanıtı:** `dosya:satır` ve ilgili kural kimliği (`AGENTS §`, `DEC-…`, `MIN-…`).
- **Ortam etiketi:** her ölçüm/kanıt `LAB-SIM` (Playwright/simüle), `LAB-DEV` (dev sunucusu), `DEVICE` (gerçek cihaz) veya `PROD` olarak etiketlenir. Farklı etiketler aynı sonuç satırında birleştirilmez.

## 6. Hüküm kuralları

- **PASS:** Kabul kriteri tanımlı, kanıt yeniden üretilebilir ve kriteri karşılıyor.
- **FAIL:** Kanıt kriteri açıkça ihlal ediyor; bulgu kaydı açıldı.
- **PENDING:** Kriter, ortam (ör. ayrılmış test DB, fiziksel cihaz), yetki veya sahip kararı eksik olduğu için değerlendirilemedi. PENDING asla PASS olarak raporlanmaz.
- **N/A:** Kapsam dışı; gerekçe yazılır.

## 7. Öneri ↔ hata ↔ kabul ayrımı

- **Kanıtlanmış hata** → `FINDINGS-REGISTER` (Tür: DEFECT).
- **Tasarım/iyileştirme önerisi** → `FINDINGS-REGISTER` (Tür: PROPOSAL); kanıt olarak kullanıcı etkisi veya ölçüm gerekir, zevk tercihi yeterli değildir.
- **Kullanıcı kabulü** yalnız sahibin açık ifadesiyle "ACCEPTED" olur. Denetçi kabul varsaymaz; uygulanmış olmak kabul edilmiş olmak demek değildir (örnek: UI-001).

## 8. Rapor şablonu (tüm skill'ler)

```
# <Skill> denetimi — <kapsam> — <tarih>
Kapsam / kapsam dışı:
Ortam: git HEAD, çalışma ağacı durumu (temiz/kirli, dosya sayısı), sunucular, test DB, ortam etiketi
Önkoşul kontrolü: <madde → PASS/PENDING>
Sonuç özeti: PASS n · FAIL n · PENDING n · N/A n
Senaryo sonuçları: <SCENARIO-MATRIX kimliği → hüküm → kanıt yolu>
Yeni/güncellenen bulgular: <FINDINGS-REGISTER kimlikleri>
Açık engeller ve sahip kararı gereken sorular:
Yapılmayanlar (bilinçli):
```

## 9. Mevcut eklentilerle görev paylaşımı

| Araç | Rol | Denetimde kullanımı |
|---|---|---|
| `impeccable` (4.3.1) | Genel görsel/UX eleştiri, polish, erişilebilirlik sezgileri, tasarım uygulaması | `tezcan-field-ux-audit` görsel hiyerarşi ve genel sezgisel eleştiri için yalnız **audit/critique** modunda danışabilir; bulguyu Tezcan kanıt formatına çevirir. Tasarım değiştiren modlar (polish, bolder, layout…) denetim sırasında kullanılmaz. |
| `ui-ux-pro-max` | Aranabilir UX kılavuzu/palet/tipografi verisi | Bir önerinin gerekçesi için referans; bulgu kaynağı değildir. |
| `frontend-design` | Yeni estetik yön | Denetimde kullanılmaz; yalnız sahip bir yeniden tasarımı kabul ettikten sonra ayrı görevde. |
| Playwright MCP | Tarayıcı otomasyonu, ekran görüntüsü, trace | UX ve performans kanıtı. Yalnız sandbox (`:8001`) veya izinli oturumla; sahibin açık kişisel sekmelerine dokunulmaz. |
| `anthropic-skills:skill-creator` | Skill yazımı/değerlendirmesi | Bu skill'lerin bakımında kullanılır; denetimde kullanılmaz. |
| `code-review`, `security-review` (yerleşik) | Diff odaklı inceleme | Değişiklik incelemesi içindir; bu skill'ler mevcut sistemin denetimidir. Aynı değişiklik için ikisi birden koşulmaz. |
