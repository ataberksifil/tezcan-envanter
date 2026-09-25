# Ölçüm protokolü

## Başlangıç ve bitiş tanımları

| Etkileşim tipi | Başlangıç | Bitiş |
|---|---|---|
| Sayfa yükleme | `page.goto` çağrısı | `load` + ana içerik seçicisi görünür (ör. `main h1`) |
| HID okutma → HTMX | okutma alanında Enter | hedef kutu (`#location-contents`, `#stock-sources`) içeriği değişti (MutationObserver veya `waitForResponse` + DOM kontrolü) |
| Form POST | gönder düğmesine tıklama | yönlenen detay sayfasının `load` olayı |

## Playwright örnek iskeleti (MCP `browser_run_code_unsafe`)

Betik dosyası `.playwright-mcp/audits/perf/` altında tutulur (git dışı). Oturum çerezi sandbox kullanıcısı için izole test DB'sinde oluşturulur; parola kullanılmaz ve rapora yazılmaz.

```js
async (page) => {
  const samples = [];
  for (let i = 0; i < 2 + N; i++) {           // ilk 2 koşu ısınma
    await page.goto(URL);
    const t0 = await page.evaluate(() => performance.now());
    // ... etkileşim ...
    await page.waitForFunction(/* bitiş koşulu */);
    const t1 = await page.evaluate(() => performance.now());
    if (i >= 2) samples.push(t1 - t0);
  }
  samples.sort((a, b) => a - b);
  const q = (p) => samples[Math.min(samples.length - 1, Math.ceil(p * samples.length) - 1)];
  return { n: samples.length, p50: q(0.5), p95: q(0.95), min: samples[0], max: samples.at(-1) };
}
```

Durum değiştiren etkileşim (POST) tekrarlanıyorsa her koşu yeni `operation_id` ile ve yalnız sandbox'ta; sahip kapsamı onaylamadıysa POST ölçümü PENDING.

## Trace

`page.context().tracing.start({ screenshots: true, snapshots: true })` → `stop({ path })`. Trace dosyası büyük olabilir; yalnız en yavaş senaryolar için.

## Raporlanacak koşullar

Tarayıcı sürümü, viewport, CPU/ağ kısıtı (varsa), sunucu tipi (runserver tek süreç), DEBUG, veri boyutu, eşzamanlı başka yük (ör. pytest) — pytest koşarken ölçüm yapılmaz.

## Etiketler

- `LAB-SIM`: Playwright + sandbox. `LAB-DEV`: dev sunucusu. `DEVICE`: gerçek telefon/tablet/okuyucu; kronometre veya cihaz içi ölçüm yöntemi yazılır.
- Laboratuvar sonucu cihazda aynı olacak diye yorumlanmaz.
