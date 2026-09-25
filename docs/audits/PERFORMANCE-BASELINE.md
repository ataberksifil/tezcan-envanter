# Performans Tabanı

- **Durum:** Ölçüm yapılmadı. Aşağıdaki tablo boş şablondur; değerler yalnız `tezcan-interaction-performance` protokolüyle doldurulur.
- **Senaryolar:** `SCENARIO-MATRIX.md` §7 (PERF-01…08). Burada tekrar tanımlanmaz.
- **Kural:** Laboratuvar (`LAB-SIM`, `LAB-DEV`) ve fiziksel cihaz (`DEVICE`) sonuçları ayrı satırlarda tutulur; biri diğerinin yerine geçmez. Tek ölçüm "taban" değildir.
- **Cihazlar (2026-09-26):** Kodscan KDS-5040 okuyucu ve Kodprint DT-482 yazıcı mevcut, henüz doğrulanmadı (SCENARIO-MATRIX DEV-01, DEV-03 PENDING). `DEVICE` ölçümü yapılmadı; cihaz satırları doğrulama ve sahip onayı sonrasına kadar PENDING.

## Ölçüm koşulları (her tur için doldurulur)

| Alan | Değer |
|---|---|
| Tarih / ölçen | |
| Git HEAD + çalışma ağacı durumu | |
| Sunucu | runserver (tek süreç, DEBUG durumu) / diğer |
| Veri seti | sandbox pilot verisi / boyut (malzeme, bakiye, hareket sayısı) |
| Tarayıcı / viewport / ağ kısıtı | |
| Isınma koşusu sayısı / ölçüm koşusu sayısı (N) | |
| Ortam etiketi | LAB-SIM / LAB-DEV / DEVICE |

## Sonuçlar

| Senaryo | Etiket | N | p50 (ms) | p95 (ms) | Sunucu yanıtı p50 (ms) | SQL sorgu sayısı | Trace / kanıt yolu | Tarih |
|---|---|---|---|---|---|---|---|---|
| PERF-01 | | | | | | | | |
| PERF-02 | | | | | | | | |
| PERF-03 | | | | | | | | |
| PERF-04 | | | | | | | | |
| PERF-05 | | | | | | | | |
| PERF-06 | | | | | | | | |
| PERF-07 | | | | | | | | |
| PERF-08 | | | | | | | | |

## Hedefler

Sayısal hedef (bütçe) henüz sahip tarafından belirlenmedi. İlk ölçüm turu yalnız tabanı kaydeder; hedef önerisi ayrı `PROPOSAL` bulgusu olarak sunulur.
