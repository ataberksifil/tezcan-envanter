# DB + media + config kurtarma doğrulaması

Kaynak: `AGENTS.md` §24, `docs/05-ARCHITECTURE.md` §30 (ADR-022). Bir yedek stratejisi, geri yüklenip doğrulanmadıkça tamamlanmış sayılmaz.

## Sıra (V1, dosya silme yokken)

1. Tutarlı PostgreSQL yedeği/snapshot (`pg_dump -Fc` veya eşdeğeri).
2. Ardından medya ve private media yedeği (düzeltme kanıtları).
3. Yapılandırma belgeleri (ayar adları, ortam değişkeni **adları**, sürüm; değerler değil).

## İzole drill (onaysız yapılabilecek tek tür)

- Hedef: ayrı, geçici bir PostgreSQL DB (sahip/IT sağlar; uygulama rolü DB oluşturamaz) ve iş klasöründe geçici medya dizini.
- Kaynak: yalnız sandbox/test verisi veya sahibin açıkça verdiği yedek. Dev DB yedeği alınacaksa sahip onayı ve salt okunur `pg_dump` gerekir.
- Hiçbir adım dev DB'ye, `var/` altındaki gerçek medya dizinine veya çalışan sunuculara yazmaz.

## Doğrulama kontrolleri (geri yüklenen ortamda)

| Kontrol | Yöntem | Beklenen |
|---|---|---|
| Ledger bütünlüğü | satır sayıları, son transaction zamanı/kimliği kaynakla eşleşir | eşit |
| Değişmezlik guard'ları | `pg_trigger` listesi, `tgenabled` | tüm guard'lar etkin |
| Projeksiyon | `manage.py verify_inventory_projection` (geri yüklenen DB'ye bağlı) | tutarlı (kaynakta BE-001 varsa aynı 4 sapma beklenir, yeni sapma yok) |
| Ek → dosya | her `CorrectionEvidence` satırının dosyası var | eksik 0 |
| Orphan dosya | depolamada DB satırı olmayan dosya | raporlanır (V1'de beklenebilir) |
| Uygulama açılışı | `manage.py check`, giriş sayfası 200 | başarılı |

## Kayıt

Drill tablosu: `adım | başlangıç–bitiş | süre | sonuç | kanıt yolu`. RTO/RPO hedefi sahip/IT tarafından belirlenmediyse ölçülen süre yalnız bilgi olarak yazılır.

## Üretim

Üretim veya pilot ortamında geri yükleme denemesi bu skill ile **yapılmaz**; ayrı yazılı onay, bakım penceresi ve IT katılımı gerekir (PENDING).
