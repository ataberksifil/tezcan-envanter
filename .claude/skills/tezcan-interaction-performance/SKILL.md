---
name: tezcan-interaction-performance
description: Tezcan Envanter etkileşim performansı ölçümü. Kontrollü tekrarlarla p50/p95, Playwright trace, HTMX istek süreleri, sayfa başına SQL sorgu sayısı ve PostgreSQL sorgu planları ile darboğaz kanıtlanır; sonuçlar docs/audits/PERFORMANCE-BASELINE.md'ye yazılır. Laboratuvar ölçümü ile fiziksel cihaz ölçümü ayrı tutulur. Yalnız kullanıcı açıkça performans/hız ölçümü istediğinde kullanılır; kod optimize etmez.
disable-model-invocation: true
argument-hint: "[DRY_RUN] [kapsam: PERF-01..08 | ekran/etkileşim adı]"
---

# Tezcan etkileşim performansı

Amaç: "yavaş" hissini tekrarlanabilir sayılara ve kanıtlanmış darboğaza çevirmek. Tek ölçüm veya tek ekran görüntüsü karar için yeterli değildir; dağılım (p50/p95) ve koşullar olmadan sayı yanıltır.

## Önce oku

1. `docs/audits/AUDIT-RUNBOOK.md` (yasaklar, izole test DB, ortam etiketleri).
2. `docs/audits/SCENARIO-MATRIX.md` §7 (PERF-01…08) ve `docs/audits/PERFORMANCE-BASELINE.md` (koşul tablosu, sonuç şablonu).
3. Ölçüm yaparken: `references/measurement-protocol.md`. Sunucu/DB darboğazı ararken: `references/server-and-db.md`.

## Kapsam sınırı

- **Sahip:** tarayıcıda algılanan süre (etkileşimden görünür sonuca), sunucu yanıt süresi, istek sayısı/boyutu, sorgu sayısı (N+1), sorgu planı, statik varlık yükü.
- **Sahip değil:** görsel/akış yargısı → `tezcan-field-ux-audit`; sorgunun doğru sonucu verip vermediği → `tezcan-backend-integrity`; mimari yeniden yapılanma → `tezcan-architecture-review`; yetkisiz erişim, oturum ve kurtarma → `tezcan-stock-data-security-audit`.
- Başka skill'in alanındaki şüpheyi bulgu olarak açma; raporun "Yönlendirme" bölümüne sahibini yaz ve `FINDINGS-REGISTER.md`'de aynı kapsam varsa yeni kayıt açma (runbook §1 çakışma önleme).

## DRY_RUN

Çağrı `DRY_RUN` ile başlarsa gerçek denetim yapılmaz; `docs/audits/AUDIT-RUNBOOK.md` §1a protokolü ve şablonu uygulanır. Yalnız talimat ve kaynak dosyaları okunur, varlıkları kontrol edilir; betik, git, DB, pytest, tarayıcı veya HTTP yok; hiçbir kayıt güncellenmez; önkoşullar `NOT_CHECKED (DRY_RUN)`.

- **Yüklenecek talimatlar:** `references/measurement-protocol.md`, `references/server-and-db.md`; SCENARIO-MATRIX §7; PERFORMANCE-BASELINE.md.
- **Gerçek koşuda kullanılacak kaynaklar:** Playwright MCP + trace (sandbox), `CaptureQueriesContext` geçici betiği (izole test DB), `EXPLAIN` (dev DB yalnız ANALYZE'sız), `.claude/skills/tezcan-backend-integrity/scripts/test_db_guard.py` (backend skill betiği).
- **Bilinen engeller:** performans bütçesi tanımsız (hükümler PENDING), ENV-001 (izole test DB yok), eşzamanlı pytest, fiziksel cihazlar mevcut fakat doğrulanmamış (aşağıda).

## Fiziksel cihaz durumu

Kodscan KDS-5040 (USB HID okuyucu) ve Kodprint DT-482 (203 DPI etiket yazıcısı) **mevcuttur, fakat henüz doğrulanmamıştır**: DEV-01 (okuma) ve DEV-03 (baskı okunabilirliği) PENDING. Cihazın var olması ölçüm kanıtı değildir. Bu cihazlarla alınacak her `DEVICE` ölçümü (ör. gerçek okutma → HTMX içerik süresi, etiket basım süresi) cihaz senaryosu doğrulanana ve sahip cihazlı oturumu onaylayana kadar **PENDING** kalır; `LAB-SIM` payload + Enter simülasyonu cihaz ölçümünün yerine geçmez ve cihaz sonucu diye raporlanmaz.

## Önkoşullar

- Hedef sunucu ve veri seti belirlendi; sandbox (`:8001`) varsayılan. Dev sunucusunda (`:8000`) yalnız GET ölçümü.
- Veri boyutu kaydedildi (malzeme, bakiye, hareket sayısı). Boyut yoksa sonuç "küçük veri tabanı" diye etiketlenir.
- Sorgu sayısı veya `EXPLAIN` için izole test DB'si veya salt okunur dev bağlantısı. `EXPLAIN ANALYZE` yalnız SELECT için ve yalnız izole test DB'sinde (ANALYZE sorguyu çalıştırır).
- Test koşulacaksa `test_db_guard.py` PASS (runbook §4). Ayrılmış DB yoksa ilgili adım PENDING.

## Yasaklar

Runbook §3'e ek olarak: kod, index, ayar veya cache ekleyerek "iyileştirmek"; `DEBUG` veya sunucu ayarını değiştirmek; yük/stres testiyle sunucuları zorlamak (bu ayrı onay ister); dev DB'de yazan `EXPLAIN ANALYZE`; laboratuvar sayısını cihaz sonucu gibi raporlamak; yeni ölçüm aracı/bağımlılık kurmak.

## Yöntem

1. PERF senaryolarını ve ölçüm sınırlarını (başlangıç olayı → bitiş koşulu) tanımla.
2. Isınma: 2 koşu atılır. Ölçüm: senaryo başına en az N=20 (hızlı HTMX için N=30).
3. Her koşu için tarayıcı süresi, ilgili isteğin sunucu süresi (`Server-Timing` yoksa istek süresi) ve yanıt boyutu kaydedilir.
4. p50/p95 hesapla (en yakın sıra yöntemi; formül raporda). Aykırı değer atılmaz, yalnız işaretlenir.
5. En yavaş iki senaryo için Playwright trace + sorgu sayısı; gerekirse `EXPLAIN (ANALYZE, BUFFERS)` (izole DB).
6. Darboğaz iddiası yalnız ölçümle desteklenirse bulgu olur (PERF- öneki). Hedef/bütçe yoksa iyileştirme PROPOSAL'dır.
7. Sonuçları PERFORMANCE-BASELINE'a, bulguları FINDINGS-REGISTER'a yaz.

## Kanıt formatı

`[PERF-02 | LAB-SIM | N=30 | 1440px | sandbox, 18 göz / 20 malzeme] p50=… ms p95=… ms; sunucu p50=… ms; sorgu=…; trace=.playwright-mcp/audits/perf/<tarih>/perf-02.zip`

## Hüküm

- **PASS:** sahibin onayladığı bütçe varsa ve p95 bütçe içinde.
- **FAIL:** bütçe aşıldı veya ölçülen N+1/tam tablo taraması kanıtlandı.
- **PENDING:** bütçe tanımsız (bugünkü durum: tüm senaryolar), cihaz ölçümü doğrulanmamış (KDS-5040 / DT-482 mevcut, DEV-01/03 PENDING), izole DB yok. Taban ölçümü PENDING'i PASS yapmaz.

## Rapor

Runbook §8 + PERFORMANCE-BASELINE koşul tablosu. Etiketi farklı ölçümler aynı satırda birleştirilmez.

## Kapsam dışı

Kod optimizasyonu, index/migration, cache katmanı (Redis yasak, `AGENTS.md` §5), yük testi, üretim izleme kurulumu, doğrulanmış cihaz ölçümü olmadan cihaz iddiası.
