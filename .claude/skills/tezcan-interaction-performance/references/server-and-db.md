# Sunucu ve PostgreSQL darboğaz analizi

## Sorgu sayısı (izole test DB)

Kod değiştirmeden, izole test DB'sinde tek seferlik bir pytest dosyası yerine **geçici** betikle ölçülür; betik denetim klasöründe kalır, ürün koduna eklenmez:

```python
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import Client
# client.force_login(...) yalnız izole test DB kullanıcısıyla
with CaptureQueriesContext(connection) as ctx:
    response = client.get(url)
print(response.status_code, len(ctx.captured_queries))
```

N+1 iddiası için: satır sayısı iki farklı veri boyutunda (ör. 10 ve 100) ölçülür; sorgu sayısı satırla doğrusal artıyorsa kanıttır.

## Sorgu planı

- Dev DB: yalnız `EXPLAIN` (ANALYZE'sız), `SET TRANSACTION READ ONLY` içinde.
- İzole test DB: `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` yalnız SELECT.
- Kanıt: plan metni, Seq Scan/Index Scan, tahmini vs gerçek satır, süre.
- Bilinen yoğun sorgular: `inventory/stock_list.py` (`annotate_minimum_stock`, `annotate_material_stock_summary`, arama `turkish_fold_expr`), hareket geçmişi listesi.

## Sunucu

- runserver tek süreçtir; eşzamanlılık ölçümü üretim davranışını temsil etmez (bulguda belirt).
- Statik dosya boyutu ve önbellek başlıkları (`?v=ui-…` sürüm parametresi).
- `pg_stat_statements` yalnız kuruluysa okunur; kurulum yapılmaz.

## Yorum kuralları

- Sorgu sayısı yüksek ama süre düşükse bulgu PROPOSAL'dır, DEFECT değil.
- Veri boyutu küçükken ölçülen süreler üretim boyutuna ekstrapole edilmez; ölçek varsayımı açıkça yazılır.
