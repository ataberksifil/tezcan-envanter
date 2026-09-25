# Tasarım eklentileriyle iş bölümü

| Araç | Bu denetimde izinli kullanım | Yasak kullanım |
|---|---|---|
| `impeccable` | Yalnız `audit` / `critique` modunda, belirli bir ekran için görsel hiyerarşi ve genel sezgisel eleştiri. Çıktı "kaynak: impeccable critique" diye etiketlenip Tezcan kanıt formatına çevrilir; tek başına bulgu kanıtı sayılmaz, tarayıcı gözlemiyle doğrulanır. | `polish`, `layout`, `bolder`, `quieter`, `colorize`, `live`, `shape` gibi dosya değiştiren veya yeni tasarım üreten modlar. |
| `ui-ux-pro-max` | Bir PROPOSAL'ın gerekçesi için kılavuz/kontrast/tipografi verisi aramak. | Bulgu üretmek; tasarım sistemi değiştirmek. |
| `frontend-design` | Kullanılmaz. | Denetim sırasında yeni estetik yön. |
| Playwright MCP | Sandbox'ta gezinme, ölçüm, ekran görüntüsü. `browser_run_code_unsafe` betik dosyaları `.playwright-mcp/` altında (git dışı). | Sahibin kişisel sekmeleri; dev sunucusunda kayıt gönderen form. |

Yeniden tasarım (ör. UI-001 çözümü) denetimden sonra, sahibin onayladığı PROPOSAL üzerine ayrı görevdir. O görevde `impeccable`/`frontend-design` kullanılabilir.
