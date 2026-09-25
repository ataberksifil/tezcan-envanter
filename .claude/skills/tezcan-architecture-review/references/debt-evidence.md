# Teknik borç kanıt ölçütleri

Bir aday ancak aşağıdakilerden **en az biri kanıtlanırsa** bulgu olur. Dosya boyutu, fonksiyon uzunluğu veya "daha temiz olurdu" tek başına kanıt değildir.

1. **Kural ihlali:** `AGENTS.md` veya bir ADR/DEC maddesini ihlal ediyor (kimlikle).
2. **Kanıtlı hata üretimi:** geçmişte hataya yol açtı (commit/bulgu/test kaydı ile) veya aynı hata sınıfı yeniden üretilebilir.
3. **Değişiklik maliyeti:** son değişikliklerde aynı mantık birden çok yerde birlikte değiştirilmek zorunda kaldı (`git log -p` ile gösterilir) ya da bir yerde değiştirilip diğerinde unutuldu.
4. **Tutarsız sonuç:** aynı kavram iki yerde farklı hesaplanıyor (ör. minimum stok hesabının tek kaynağı `DEC-042` öncesi iki yerdeydi).
5. **Test edilemezlik:** kritik bir kural, yapı nedeniyle izole test edilemiyor (deneme ile gösterilir).
6. **Gereksiz karmaşıklık:** kullanılmayan soyutlama/parametre/kod yolu (çağıran yok — arama çıktısıyla).

## Değerlendirme tablosu

| Aday | Ölçüt no | Kanıt | Maliyet | En küçük adım | Risk (değişiklik yapılırsa) |
|---|---|---|---|---|---|

## Hatırlatmalar

- `inventory/views.py`, `inventory/forms.py` büyük dosyalardır; bu gözlem tek başına bulgu değildir.
- Okuma amaçlı çapraz import (ör. `catalog/views.py` → `inventory.stock_list`) `AGENTS.md` §7'de yasak değildir; yalnız mutasyon yasaktır. Yine de çift yönlülük nedeniyle not edilir.
- Yönetim/demo komutlarının (`core/management/commands/seed_demo_environment.py`) §7 kapsamına girip girmediği yorum sorusudur → PENDING + sahip sorusu.
