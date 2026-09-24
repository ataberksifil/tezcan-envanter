# UI Tasarım Sistemi — Tezcan Envanter

Bu belge UI modernizasyonunun görsel ve etkileşim sözleşmesidir. Uygulama `core/static/core/css/app.css`, `core/static/core/js/app.js`, `core/templatetags/ui.py` ve `core/templates/` altındaki ortak parçalardadır. İş kuralları için yetkili belgeler `docs/00`–`06` ve `AGENTS.md`'dir; bu belge onları değiştirmez.

## 1. Yön

Gemini araştırmasının önerdiği **Direction B — Modern Frontline Utility**: masaüstünde yoğun tablolar, mobilde büyük dokunma hedefleri, rengin yalnız anlam taşıması, fiziksel konumun birinci sınıf veri olması.

Kaynak ayrımı:

| Karar | Kaynak |
|---|---|
| Konum kodu her tabloda/kartta yüksek kontrastla; kondisyon satırları birleştirilmez; renk yalnız 4 anlamda (kırmızı/sarı/yeşil/gri) + mavi eylem; masaüstü `table-sm` yoğunluğu, mobil 44–48 px hedef; sabit tablo başlığı; sağa yaslı eş genişlikli miktarlar; kaynak→hedef zıt vurgulu blok ve ok; mobil alt gezinme + masaüstü sol menü; üst arama; rol rozeti; eylemli boş durum; çift tıklamayı engelleyen buton durumu; modal içinde uzun form yok | Gemini araştırması |
| Önizleme → açık onay, operation_id idempotency, kör sayım, düzeltme/kanıt akışı, kullanım yeri ≠ stok konumu, staging → TRANSFER | Onaylı ürün/iş kuralları (`AGENTS.md`, `DEC-0xx`) |
| IBM Plex Sans/Mono, "raf etiketi" konum plakası, sarı marka işareti, koyu kurşuni kenar menü, TZ1 etiketini tarayıcıda çözüp listeden seçen okutma yardımcısı, önizlemede alanların kilitlenmesi | Bu programın tasarım kararı |
| Ana sayfa önceliği (okut/ara → 3 günlük işlem → Talep/SKT → geri kalanı menüde), çentiksiz aktif menü, tek ortak okut/ara davranışı, ikincil bilgi açılır bölümde, Türkçe miktar gösterimi (`80`, `2,5`, `1.250,75`) | Kullanıcı revizyon geri bildirimi (Faz 1) |
| Vendored Bootstrap 5.3.8 korunur; Tabler vb. eklenmez; varlıklar yerel (intranet) | Kod tabanı kısıtı |

Araştırmadan bilinçli sapmalar:

- **Onay butonu rengi** hareket türüne göre (giriş yeşil, çıkış kırmızı) değil, tek birincil mavidir. Kırmızı bu sistemde "tehlike/yıkıcı" anlamına ayrılmıştır; normal çıkış yıkıcı değildir. Hareket türü başlık/ikon rengiyle belirtilir.
- **Global klavye dinleyicisi** (sayfanın herhangi bir yerinde tarama yakalama) uygulanmaz: operasyon formlarında yanlış alana yazma riskini artırır. Bunun yerine odaklı okutma alanları + üst arama çubuğu TZ1 tanır.
- **Sesli geri bildirim** eklenmez (atölye gürültüsü, tarayıcı otomatik oynatma kısıtları); görsel yeşil/kırmızı çerçeve ve metin kullanılır.
- **Kondisyon düğmeleri (radyo)** ve **+/- miktar butonları** mevcut form alanlarının yerine konmaz; kondisyon listesi DB'den dinamik gelir ve miktar ondalıklı olabilir.
- **Anlık HTMX yetersiz stok doğrulaması** eklenmez; yetkili doğrulama servistedir, form sonrası anlaşılır hata gösterilir.
- **HTMX ile sekmeli detay / canlı arama** yalnız fayda açık olduğunda; varsayılan GET form + sayfa yenileme (sunucu otoritesi, geri tuşu doğru çalışır).

## 2. Tokenlar

`:root` üzerinde `--tz-*`; Bootstrap değişkenleri bunlara köprülenir.

| Token | Değer | Kullanım |
|---|---|---|
| `--tz-ink` | `#17212b` | Ana metin |
| `--tz-ink-2` | `#45515d` | İkincil metin (beyaz üzerinde ≥ 7:1) |
| `--tz-canvas` | `#eef1f4` | Sayfa zemini |
| `--tz-surface` | `#ffffff` | Panel, tablo, form alanı |
| `--tz-line` / `--tz-line-strong` | `#d6dce2` / `#a9b4be` | Ayırıcı / form kenarı |
| `--tz-shell` | `#1b2631` | Kenar menü, ana sayfa arama bandı |
| `--tz-primary` | `#1a5da8` | Tek eylem rengi, bağlantı, odak |
| `--tz-success` | `#176b3a` | Artış, başarı, Yeni/Sağlam |
| `--tz-warning` | `#8a4a00` (+ `#d98e1f` kenar) | Önizleme, SKT yaklaşan, kaynak konum |
| `--tz-danger` | `#b0261b` | Hata, azalış, arızalı, SKT geçmiş |
| Marka sarısı | `#f2b418` | Yalnız marka işareti ve ana sayfa "Ara" butonu. Menüde çizgi/çentik olarak **kullanılmaz** |

Tipografi: IBM Plex Sans 400/500/600/700 (latin + latin-ext, Türkçe glifler), IBM Plex Mono 500/600 yalnız **kod ve miktar** için. Gövde 15 px; H1 24 px/600; tablo 14 px.

Aralık: 4 px tabanlı (Bootstrap spacer). Radius 6 px (buton/panel), 4 px (menü öğesi), 3 px (konum plakası), tam yuvarlak (durum etiketi).

## 3. Bileşenler

| Sınıf / parça | Anlam |
|---|---|
| `.app-shell`, `.app-sidebar.offcanvas-lg`, `.app-topbar`, `.bottom-nav` | Kabuk. ≥992 px sabit sol menü; altında offcanvas + alt gezinme (Ana sayfa, Stok, Tara, Menü — yetkiye göre) |
| Menü grupları | Ana sayfa · **Günlük işler** (Mevcut Stok, Stok Girişi + alt öğe Tekil varlık, Stok Transferi, Stok Çıkışı, Stok İadesi) · **Kayıtlar ve takip** (Malzemeler, Envanter hareketleri, Talep Takip, SKT) · **Kontrol** (Fiziksel sayım, Düzeltme talepleri, Kesim) · **Diğer** (Kamera ile tara, Yönetim). Görünürlük mevcut izin koşullarıyla aynıdır |
| Aktif menü öğesi | Satır boyu yumuşak zemin (`rgba(143,190,245,.16)`), 600 ağırlık, ikon `#8fbef5`. **Sol şerit/çentik yok.** Klavye odağı ayrı: 2 px beyaz halka. Alt gezinmede aktif ikon arkasında yumuşak "pill" + mavi metin |
| Ana sayfa | 1) büyük "Barkodu okut veya malzeme ara" alanı (sayfa açılışında odak; sonra odak çalınmaz), 2) Günlük işlemler: Mevcut Stok, Mal Kabul, Yerleştir / Transfer Et (girişi olmayan rolde Stok Çıkışı), 3) Takip: Talep Takip ve SKT sayıları (yetkiye göre). Yetkisi olmayan kullanıcı açıklama görür |
| `.page-head` (+ `.page-head-sub`, `.page-actions`, `.breadcrumb-lite`) | Sayfa başlığı |
| `.panel`, `.panel-head`, `.panel-body` | Tek katman yüzey; iç içe kart yok |
| `.loc` (+ `.loc-path`) | **Konum plakası** — raf etiketi görünümü; her konum gösteriminde |
| `.code` | Malzeme/varlık kodu (mono) |
| `.qty` + `.qty-unit`, filtre `|qty` | Miktar: mono, sağa yaslı; `80.000 → 80`, `2.500 → 2,5`, `1250.750 → 1.250,75` — binlik nokta, ondalık virgül, anlamlı basamaklar korunur. Yalnız sunum; saklanan `Decimal`, hesaplar ve form parse davranışı değişmez |
| `.tag` + `.tag-success/info/warning/danger/neutral` | Durum etiketi |
| `.tag.cond-<CODE>` | Kondisyon: NEW_GOOD yeşil, USED_REMOVED_GOOD mavi, DEFECTIVE / USED_REMOVED_DEFECTIVE kırmızı; bilinmeyen kod nötr |
| `.mv.mv-<TYPE>` | Hareket türü: giriş/iade/açılış yeşil, çıkış kırmızı, transfer mavi, düzeltme/sayım sarı |
| `.table-wrap` + `.table-data` (+ `.table-stack`) | Veri tablosu; sabit başlık; <768 px'te `data-label` ile kayıt kartına dönüşür (`stack-full`, `stack-hide`) |
| `.filter-bar` | Liste filtreleri; "Temizle" yalnız filtre varken |
| `core/_field.html` | Form alanı: etiket, "(isteğe bağlı)", yardım, hata |
| `.form-section` | Uzun formu anlamlı bölümlere ayırır |
| `details.disclosure` | İkincil/isteğe bağlı bilgi (ör. Paket ve SKT); değer veya hata varsa açık gelir |
| `details.filter-more` | Liste ekranlarında ikincil filtreler ("Diğer filtreler", etkinse açık) |
| `core/_pick_field.html` (`.pick`) | Tek denetim: listeden seç + aynı satırda "Etiket okut" kutusu |
| `.mode-switch` | Aynı işin iki türü arasında geçiş (Adetli malzeme / Tekil varlık) |
| `.form-actions` | Birincil eylem; mobilde alt gezinmenin üstüne yapışık |
| `.review` + `.kv` | **Önizleme**: "henüz stok değişmedi"; alanlar gizli taşınır, yalnız Onayla / Geri dön |
| `.flow` (`.flow-source` → `.flow-target`) | Kaynak (sarı üst çizgi) → hedef (yeşil üst çizgi) |
| `.empty` | Eylemli boş durum |
| `core/_messages.html` | Django mesajları, ikonlu |
| `templates/403.html`, `404.html`, `500.html` | Kabuk içinde anlaşılır hata sayfaları |

## 4. Tarama ve klavye

- **Kodscan KDS-5040 (HID)**: okunan metin + Enter.
- `data-scan-field` alanında Enter **asla formu göndermez**; odak sonraki alana geçer.
- **Tek ortak okut/ara davranışı** (`data-scan-search`): ana sayfa alanı, üst çubuk alanı ve liste arama alanları aynı kuralı izler — `TZ1` ile başlayan her değer salt okunur `/identification/resolve/` çözümleyicisine gider (bozuk/bilinmeyen kod → sunucunun açık mesajı; yetkisiz → 403 açıklaması); diğer metin arama olur. Kullanıcı "hangi alana okutmalıyım" diye düşünmez. Ana sayfada üst çubuk alanı gösterilmez (tek birincil alan). `/` tuşu, kullanıcı başka bir alanda yazmıyorsa arama alanına odaklar.
- `core/_pick_field.html` (`data-scan-select`, `data-scan-kind="M|A|L"`): TZ1 yükü tarayıcıda UUID'ye çözülür ve sunucunun zaten listelediği seçenek seçilir. Yanlış tür, listede olmayan (pasif/uygun olmayan) kayıt ve TZ1 olmayan metin anlaşılır mesajla reddedilir. İstek/mutasyon yoktur; sunucu doğrulaması aynen geçerlidir.
- Çift gönderim koruması: POST formlarında gönderimden sonra butonlar devre dışı (`aria-busy`); `name/value` taşıyan niyet butonları korunur; geri/ileri önbellekten dönüşte açılır.
- Odak: 3 px mavi çerçeve, `:focus-visible`. "İçeriğe geç" bağlantısı.

## 5. Yazdırma

Kabuk (menü, üst çubuk, alt gezinme, eylem çubuğu) `@media print` ile gizlenir. Etiket sayfaları kendi `identification/css/label.css` düzenini kullanır (Kodprint DT-482, 203 DPI, ≤104 mm).

## 6. Kurallar

- Renk yalnız anlam içindir; dekoratif gradyan, gölge yığını, iç içe kart yok.
- Konum her zaman `.loc` plakası; kullanım yeri (hat/uygulama) asla plaka olarak gösterilmez.
- Mutasyon yapan her işlem önizleme/onay adımını korur; GET ve "Geri dön" stok değiştirmez.
- Varlıklar yereldir (CDN yok). `app.css`/`app.js` değişince `base.html`'deki `?v=ui-N` sürümü artırılır.
- Kullanıcıya görünen metin Türkçe; kod/sınıf adları İngilizce.
- Rol etiketi yalnız görüntü içindir; girişte oturuma yazılır (`core/ui_session.py`), yetkilendirme okumaz; rol değişikliği sonraki girişte görünür.
- Aynı işi yapan ikinci buton/kart eklenmez; ikincil bilgi `details` ile açılır; bir işlem her yerde aynı adla anılır.
- Seçim listelerinde UUID gösterilmez; yalnız tekrar eden kod/ad varsa kısa `#xxxxxxxx` ayırt edicisi eklenir.

## 7. Vendored varlıklar

| Varlık | Konum | Lisans |
|---|---|---|
| IBM Plex Sans / Mono (woff2 alt kümeleri) | `core/static/vendor/ibm-plex/` | SIL OFL 1.1 |
| Bootstrap Icons 1.13.1 — 55 ikonluk SVG sprite alt kümesi | `core/static/vendor/bootstrap-icons/icons.svg` | MIT |

Python/JS bağımlılığı eklenmemiştir.
