# Salt okunur kanıt sorguları

Dev DB'de her sorgu bu kalıpta çalıştırılır; hiçbir yazma yapılmaz ve işlem geri alınır:

```python
# .venv\Scripts\python.exe manage.py shell -c "exec(open(r'<betik>').read())"
from django.db import connection, transaction
with transaction.atomic():
    with connection.cursor() as c:
        c.execute("SET TRANSACTION READ ONLY")
        # ... SELECT ...
    transaction.set_rollback(True)
```

Betik dosyaları iş/oturum geçici klasöründe tutulur (ör. `$CLAUDE_JOB_DIR/tmp`); proje içinde `inspect.py` gibi standart kütüphane adlarıyla dosya oluşturulmaz (Python modül gölgelemesi).

## Hazır sorgular

1. Projeksiyon doğrulaması: `manage.py verify_inventory_projection` (komutun kendisi salt okunur).
2. Negatif bakiye: `SELECT count(*) FROM inventory_stockbalance WHERE quantity < 0;` (beklenen 0; constraint de var).
3. Ledger'sız bakiye (BE-001 benzeri): `StockBalance` satırı olup aynı `(material, location, condition)` için hiç `InventoryTransactionLine` bulunmayanlar.
4. Tekil varlık durumu tutarlılığı: `IN_STOCK` ve `current_location IS NULL` → 0; `ISSUED` ve `current_location IS NOT NULL` → 0.
5. `operation_id` tekrar: `SELECT operation_id, count(*) FROM inventory_inventorytransaction GROUP BY 1 HAVING count(*) > 1;` → 0.
6. Trigger varlığı: `SELECT tgname, tgrelid::regclass, tgenabled FROM pg_trigger WHERE NOT tgisinternal;` — `tgenabled` `D` (disabled) olmamalı.

Kişisel veri (çalışan adı/sicil) rapora yazılmaz; yalnız sayılar.
