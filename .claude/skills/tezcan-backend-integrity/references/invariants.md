# Stok bütünlüğü değişmezleri

Kaynak: `AGENTS.md` bölüm numaraları parantezde. Her madde için kod izi + test izi + (varsa) DB guard aranır.

## Ledger ve projeksiyon (§9)

- Tamamlanmış `InventoryTransaction`/`Line` UPDATE/DELETE → DB trigger `inventory_ledger_immutability_guard` (migration `inventory/0002`). Admin, `queryset.update`, `bulk_update`, raw SQL de kesilmeli.
- Transaction en az bir satır: `inventory_tx_requires_line_guard` (deferred).
- `StockBalance` yalnız QUANTITY malzeme + stok tutan konum: `inventory_balance_quantity_guard`.
- Tracking mode geçmiş sonrası değişemez: `inventory_material_tracking_mode_guard`.
- Projeksiyon ledger'dan üretilebilir: `manage.py verify_inventory_projection` (salt okunur). Bilinen sapma: BE-001.

## Yön ve şekil (§8, §10)

- `source_location` azaltır, `target_location` artırır; ikisi doluysa farklı (DB check + servis).
- Quantity ve serialized satır şekilleri karışmaz; serialized satırda quantity/unit NULL, `asset_event_seq` monotonik.
- Tekil varlık tek konumda; `IN_STOCK` ⇔ konum dolu, `ISSUED` ⇔ konum boş (model check).

## Eşzamanlılık (§11)

- Sıra: yetki → master ön kontrol → transaction → `operation_id` → kilit → yeniden oku → yeniden doğrula → ledger → projeksiyon → ilişkili kayıtlar → commit.
- Kilit sırası: `material_id → location_id → condition_id → pk`.
- İlk `(material, location, condition)` satırı: UNIQUE + güvenli insert/on-conflict + yeniden okuma/kilit.
- Zorunlu yarış testleri: son stok, tekil hareket, düzeltme çift onay, mutabakat çift commit, import çift commit, baseline çift kuruluş.

## Idempotency (§12)

- `operation_id` UNIQUE; aynı ID + aynı fingerprint → önceki sonuç; farklı fingerprint → çatışma, stok etkisi yok.
- Fingerprint sunucuda, SHA-256, semantik alanlar; client hash'ine güvenilmez.
- Form ön kontrolleri replay'i engellememeli (örnek: tekil kabul tekrar gönderimi, 2026-09-25 yerel, commit edilmemiş düzeltme).

## Düzeltme, sayım, import, baseline (§14–15)

- `PENDING` düzeltme stok değiştirmez; onay yalnız ADMIN_MANAGER; talep sahibi kendi talebini onaylayamaz; negatif birikim sıfırın altına inemez.
- Sayım `StockBalance`'a doğrudan yazmaz; drift kontrolü; sayan kişi kendi farkını onaylayamaz.
- Import commit sıfır ledger/bakiye; aday veri stok değildir.
- `INITIAL_BALANCE` yalnız baseline kuruluşunda, bir kez.

## Minimum stok (`DEC-042`) — yerel, commit edilmemiş

Karar ve uygulama yalnız yerel çalışma ağacında; commit edilene kadar denetim raporunda "yerel" diye ayrılır.


- Tek hesap `inventory/stock_list.py::annotate_minimum_stock`; sayılan kondisyonlar `catalog/conditions.py`; sınıflandırılmamış kondisyon sayılmaz; tekilde `IN_STOCK` varlık sayısı; salt okunur.

## Servis dışı yazım araması (grep)

```
rg -n "StockBalance\.objects\.(create|update|filter\(.*\)\.update|bulk_)" --glob "!**/tests/**" --glob "!**/migrations/**"
rg -n "\.quantity\s*[+-]?=" --glob "*.py" --glob "!**/tests/**"
rg -n "current_location\s*=|current_state\s*=" --glob "*.py" --glob "!**/tests/**"
```

Her eşleşmenin `inventory/services/` içinde, kilit altında olduğu doğrulanır.
