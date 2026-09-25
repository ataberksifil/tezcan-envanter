---
name: tezcan-architecture-review
description: Tezcan Envanter mimari incelemesi. Modüler monolitin modül sahipliği ve bağımlılık kuralları (AGENTS.md bölüm 6-7), çift yönlü importlar, katman ihlalleri, gereksiz karmaşıklık ve yalnız kanıtlanmış teknik borç raporlanır. Yalnız kullanıcı açıkça mimari veya teknik borç incelemesi istediğinde kullanılır; refactor yapmaz, mimariyi değiştirmez, büyük dosya tek başına bulgu değildir.
disable-model-invocation: true
argument-hint: "[DRY_RUN] [kapsam: bagimliliklar | modul-adi | teknik-borc-adayi]"
---

# Tezcan mimari incelemesi

Amaç: mevcut modüler monolitin (`AGENTS.md` §4) sınırlarının korunup korunmadığını ve **bir maliyeti kanıtlanmış** teknik borcu göstermek. Mimari değişiklik önerisi değil, kanıt üretilir; öneri yalnız PROPOSAL olur ve karar sahibindir (`AGENTS.md` §1: alt seviye teknik karar iş kuralını sessizce değiştiremez).

## Önce oku

1. `AGENTS.md` §4–7 (stack, yasak genişleme, modül sahipliği, bağımlılık güvenliği), `docs/05-ARCHITECTURE.md` ilgili ADR'ler, `docs/06-DECISION-REGISTER.md`.
2. `docs/audits/AUDIT-RUNBOOK.md` ve `FINDINGS-REGISTER.md`.
3. Borç kanıt ölçütleri: `references/debt-evidence.md`.

## Kapsam sınırı

- **Sahip:** modül sınırı ve import yönü, döngüler, katman ihlali (view'de iş kuralı, servis dışı mutasyon **yapısı**), yinelenen mantık, ölü kod, gereksiz soyutlama, bağımlılık listesi (`requirements*.txt`) ile `AGENTS.md` §5 uyumu.
- **Sahip değil:** mutasyonun doğru sonuç verip vermediği → `tezcan-backend-integrity`; güvenlik açığı → `tezcan-stock-data-security-audit`; hız → `tezcan-interaction-performance`; UX → `tezcan-field-ux-audit`.
- Başka skill'in alanındaki şüpheyi bulgu olarak açma; raporun "Yönlendirme" bölümüne sahibini yaz ve `FINDINGS-REGISTER.md`'de aynı kapsam varsa yeni kayıt açma (runbook §1 çakışma önleme).

## DRY_RUN

Çağrı `DRY_RUN` ile başlarsa gerçek denetim yapılmaz; `docs/audits/AUDIT-RUNBOOK.md` §1a protokolü ve şablonu uygulanır. Yalnız talimat ve kaynak dosyaları okunur, varlıkları kontrol edilir; betik, git, DB, pytest, tarayıcı veya HTTP yok; hiçbir kayıt güncellenmez; önkoşullar `NOT_CHECKED (DRY_RUN)`.

- **Yüklenecek talimatlar:** `references/debt-evidence.md`; AGENTS.md §4–7; docs/05-ARCHITECTURE.md ADR'leri.
- **Gerçek koşuda kullanılacak kaynaklar:** `scripts/module_imports.py` (ast taraması), `git log -p` (değişiklik maliyeti kanıtı), `rg` sahiplik araması.
- **Bilinen engeller:** kural yorumu belirsiz maddeler (ör. demo seed komutu) için sahip cevabı.

## Önkoşullar

- Git HEAD ve çalışma ağacı durumu kaydedildi; incelenen kodun commit'li mi uncommitted mı olduğu raporda ayrılır.
- Veritabanı gerekmez. Test gerekirse runbook §4.

## Yasaklar

Runbook §3'e ek olarak: dosya taşımak/bölmek/yeniden adlandırmak, yeni katman/paket/framework önermek için kanıtsız gerekçe (ör. "dosya 1300 satır"), `AGENTS.md` §5 listesindeki yasak teknolojileri (SPA, microservice, Redis/Celery, CQRS, ayrı REST API…) çözüm olarak önermek, mimari kararı "bulgu" kılığında değiştirmek.

## Yöntem

1. **Bağımlılık haritası:** `python .claude/skills/tezcan-architecture-review/scripts/module_imports.py` (salt okunur `ast` taraması; çıkış 1 = yasak import var). Çıktıyı kanıt olarak ekle; her ihlali dosyada doğrula (yönetim komutu mu, runtime mı?).
2. **Sahiplik:** her modülün yazdığı modelleri çıkar; `AGENTS.md` §6 tablosundaki "Yasak" sütunuyla karşılaştır.
3. **Döngüler:** çift yönlü çiftleri incele; okuma amaçlı mı, mutasyon mu, lazy import mu?
4. **Borç adayları:** `references/debt-evidence.md` ölçütlerinden en az biri kanıtlanmadıkça bulgu açma.
5. **Kayıt:** ARCH- önekli bulgular; kural kimliği (`AGENTS §7`, `ADR-0xx`) zorunlu.

## Kanıt formatı

`[ARCH | kural: AGENTS §7 "core hiçbir business modülüne bağımlı olmaz" | dosya:satır | script çıktısı satırı] gözlem → maliyet kanıtı`

## Hüküm

- **PASS:** kural için ihlal yok (script + manuel doğrulama).
- **FAIL:** kural ihlali doğrulandı (yalnız script eşleşmesi yetmez; dosyada bakılır).
- **PENDING:** kuralın yorumu belirsiz (ör. demo seed komutu kapsama giriyor mu) → sahip sorusu olarak yazılır.

## Rapor

Runbook §8 + bağımlılık tablosu (runtime / test / migration ayrı) + borç adayları tablosu (`aday | kanıtlanmış maliyet | önerilen en küçük adım | risk`).

## Kapsam dışı

Refactor uygulaması, performans ölçümü, güvenlik taraması, stil/lint tartışması, "modern mimari" önerileri.
