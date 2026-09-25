---
name: tezcan-backend-integrity
description: Tezcan Envanter stok bütünlüğü denetimi. Değişmez ledger, StockBalance ve SerializedAsset projeksiyonları, negatif stok, eşzamanlılık ve kilit sırası, idempotency (operation_id + request_fingerprint), servis katmanı yetkisi, sayım/mutabakat, düzeltme, baseline ve PostgreSQL guard/trigger'ları kod, test ve salt okunur sorgu kanıtıyla denetlenir. Yalnız kullanıcı açıkça backend/stok doğruluğu denetimi istediğinde kullanılır; veri onarmaz, kod değiştirmez.
disable-model-invocation: true
argument-hint: "[DRY_RUN] [kapsam: BE-T-01..16 | ledger | idempotency | sayım | düzeltme | baseline | BE-001]"
---

# Tezcan backend bütünlüğü

Amaç: "sistem bir malzemenin orada olduğunu söylüyorsa oradadır" vaadini (`AGENTS.md` §3) tehdit eden her yolu kanıtla bulmak. Öncelik: veri bütünlüğü > stok doğruluğu > denetlenebilirlik.

## Önce oku

1. `AGENTS.md` §8–15 ve §20 (servis katmanı, ledger, projeksiyon, eşzamanlılık, idempotency, düzeltme, sayım/baseline, zorunlu testler), ilgili `DEC-0xx` (`docs/06-DECISION-REGISTER.md`).
2. `docs/audits/AUDIT-RUNBOOK.md` (yasaklar, **izole test DB kuralı**).
3. `docs/audits/SCENARIO-MATRIX.md` §1–2 (ACC-, BE-T-) ve `FINDINGS-REGISTER.md` (BE-001, BE-002, ENV-001, ENV-002).
4. Kontrol listesi: `references/invariants.md`. Kanıt sorguları: `references/read-only-queries.md`.

## Kapsam sınırı

- **Sahip:** stok matematiği, ledger değişmezliği, projeksiyon ↔ ledger tutarlılığı, kilit/sıra/yarış, idempotency, servis katmanında yetki ve invariant doğrulaması, sayım/düzeltme/baseline iş akışı kuralları, DB guard'ları, test güvenilirliği.
- **Sahip değil:** saldırgan bakışıyla erişim/IDOR/CSRF → `tezcan-stock-data-security-audit` (servis izin kontrolünün **varlığı** buradadır, istismar denemesi orada); modül yapısı → `tezcan-architecture-review`; hız → `tezcan-interaction-performance`; ekran ve akış kullanılabilirliği → `tezcan-field-ux-audit`.
- Başka skill'in alanındaki şüpheyi bulgu olarak açma; raporun "Yönlendirme" bölümüne sahibini yaz ve `FINDINGS-REGISTER.md`'de aynı kapsam varsa yeni kayıt açma (runbook §1 çakışma önleme).

## DRY_RUN

Çağrı `DRY_RUN` ile başlarsa gerçek denetim yapılmaz; `docs/audits/AUDIT-RUNBOOK.md` §1a protokolü ve şablonu uygulanır. Yalnız talimat ve kaynak dosyaları okunur, varlıkları kontrol edilir; betik, git, DB, pytest, tarayıcı veya HTTP yok; hiçbir kayıt güncellenmez; önkoşullar `NOT_CHECKED (DRY_RUN)`.

- **Yüklenecek talimatlar:** `references/invariants.md`, `references/read-only-queries.md`; AGENTS.md §8–15, §20; SCENARIO-MATRIX §1–2; FINDINGS-REGISTER BE-001, BE-002, ENV-001, ENV-002.
- **Gerçek koşuda kullanılacak kaynaklar:** `scripts/test_db_guard.py`, pytest (yalnız ayrılmış test DB), `manage.py verify_inventory_projection`, salt okunur SQL (dev DB), `rg` servis dışı yazım araması.
- **Bilinen engeller:** ENV-001 (ayrılmış test DB yok), guard FAIL, sandbox aynı test DB'sinde açık.

## Önkoşullar

- Git HEAD ve çalışma ağacı durumu kaydedildi.
- Dev DB'de yalnız salt okunur sorgular (`SET TRANSACTION READ ONLY`, işlem sonunda rollback).
- Pytest yalnız ayrılmış izole test DB'sinde. Hedef DB `POSTGRES_TEST_DB` ile **aynı PowerShell oturumunda** ayarlanır, guard aynı hedefle pytest'ten **önce** koşar ve pytest yalnız guard çıkışı 0 ise başlar. Guard, `POSTGRES_TEST_DB` boşsa veya `--db` ile farklıysa FAIL verir. Bash tarzı satır içi önek (`POSTGRES_TEST_DB=… pytest`) PowerShell'de çalışmaz; kullanılmaz.

  ```powershell
  $env:POSTGRES_TEST_DB = 'test_tezcan_envanter_audit'
  .venv\Scripts\python.exe .claude\skills\tezcan-backend-integrity\scripts\test_db_guard.py --db $env:POSTGRES_TEST_DB
  if ($LASTEXITCODE -eq 0) { .venv\Scripts\python.exe -m pytest <hedef testler> } else { 'guard FAIL: pytest koşulmadı (PENDING)' }
  ```

  FAIL → test koşulmaz, adım PENDING. Ayrılmış DB (ENV-001) yoksa DB gerektiren tüm adımlar PENDING.
- Eşzamanlılık iddiaları yalnız gerçek PostgreSQL testleriyle; SQLite veya mock kanıt sayılmaz.

## Yasaklar

Runbook §3'e ek olarak: BE-001'deki dört bakiyeyi onarmak/silmek; `verify_inventory_projection` dışında bir onarım/rebuild komutu çalıştırmak; trigger/constraint devre dışı bırakmak; dev DB'de `EXPLAIN ANALYZE` dahil yazma riski taşıyan komut; sandbox'ın kullandığı `test_tezcan_envanter` DB'sinde sandbox açıkken pytest; başarısız testi "kararsız" diye yok saymak (BE-002 gibi kayda geçirilir).

## Yöntem

1. Kapsamı BE-T/ACC kimliklerine çevir.
2. **Kod izi:** her kural için mutasyon yolu (view/form → servis → kilit → ledger → projeksiyon) `dosya:satır` ile çıkarılır; servis dışı `StockBalance`/`SerializedAsset` yazımı aranır (`references/invariants.md` §grep).
3. **Test izi:** kuralı koruyan testi bul; yoksa "açık eksik" (ör. BE-T-16). Test var ama iddia zayıfsa PROPOSAL.
4. **DB guard izi:** migration'daki trigger/constraint'in kuralı gerçekten kestiğini oku; mümkünse izole test DB'sinde negatif test ile doğrula.
5. **Salt okunur veri kanıtı:** `verify_inventory_projection` ve `references/read-only-queries.md`.
6. **Hüküm ve kayıt:** BE- önekli bulgular; bilinen BE-001/BE-002'yi yeniden açma, yalnız yeni kanıt ekle.

## Kanıt formatı

`[BE-T-02 | kod: inventory/services/issues.py:NN | test: path::name | DB: test_tezcan_envanter_audit | LAB-DEV] gözlem`

## Hüküm

- **PASS:** kural kodda + testte (gerçek PostgreSQL) + gerekiyorsa DB guard'da kanıtlı ve test izole DB'de geçti.
- **FAIL:** kural ihlali yeniden üretildi, veri tutarsızlığı bulundu veya test tekrarlı başarısız.
- **PENDING:** izole DB yok, test koşulamadı, yalnız kod okuması var (kod okuması tek başına PASS değildir).

## Rapor

Runbook §8 + her BE-T için satır: `kimlik | kod izi | test izi | DB guard | hüküm`.

## Kapsam dışı

Veri onarımı (ayrı, onaylı görev), servis refactor'ı, yeni migration/constraint, üretim DB erişimi, performans ayarı.
