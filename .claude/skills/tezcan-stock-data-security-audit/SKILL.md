---
name: tezcan-stock-data-security-audit
description: Tezcan Envanter stok verisi güvenlik denetimi. Rol ve nesne düzeyinde erişim, IDOR, CSRF (HTMX dahil), oturum/cookie, sır yönetimi, düzeltme kanıtı ve medya erişimi, PostgreSQL rol yetkileri, Django dağıtım ayarları ve DB+media+config yedek/geri yükleme doğrulaması kanıtla incelenir. Yalnız kullanıcı açıkça güvenlik veya kurtarma denetimi istediğinde kullanılır; üretim ortamında deneme yapmaz, sır göstermez, kod değiştirmez.
disable-model-invocation: true
argument-hint: "[DRY_RUN] [kapsam: SEC-01..12 | rol-erisim | idor | csrf | medya | postgres-yetki | deploy | restore-drill]"
---

# Tezcan stok verisi güvenliği

Amaç: yetkisi olmayan birinin stok verisini **görmesini, değiştirmesini veya kanıtı ele geçirmesini** sağlayan yolları ve felaket sonrası verinin gerçekten geri gelip gelmediğini kanıtla göstermek.

## Önce oku

1. `AGENTS.md` §13 (izinler), §17 (Admin), §18 (dosyalar/QR), §19 (güvenlik), §24 (yedekleme), `docs/05-ARCHITECTURE.md` §29–30.
2. `docs/audits/AUDIT-RUNBOOK.md`, `SCENARIO-MATRIX.md` §6 (SEC-01…12), `FINDINGS-REGISTER.md`.
3. Kontrol listesi: `references/access-and-web.md`. Kurtarma doğrulaması: `references/recovery-verification.md`.

## Kapsam sınırı

- **Sahip:** kimlik/rol/nesne erişimi, IDOR ve enumeration, CSRF, oturum, sırlar, kanıt/medya dosyası erişimi, PostgreSQL rol ve yetkileri, `check --deploy` bulguları, yedek/geri yükleme doğrulaması, Excel formül enjeksiyonu.
- **Sahip değil:** stok matematiği ve servis invariant'ları → `tezcan-backend-integrity`; modül yapısı → `tezcan-architecture-review`; hız ve sorgu maliyeti → `tezcan-interaction-performance`; ekranda yetki mesajlarının anlaşılırlığı → `tezcan-field-ux-audit`. Servis katmanında izin kontrolünün **varlığı** backend'dedir; onu atlatma **denemesi** buradadır.
- Başka skill'in alanındaki şüpheyi bulgu olarak açma; raporun "Yönlendirme" bölümüne sahibini yaz ve `FINDINGS-REGISTER.md`'de aynı kapsam varsa yeni kayıt açma (runbook §1 çakışma önleme).

## DRY_RUN

Çağrı `DRY_RUN` ile başlarsa gerçek denetim yapılmaz; `docs/audits/AUDIT-RUNBOOK.md` §1a protokolü ve şablonu uygulanır. Yalnız talimat ve kaynak dosyaları okunur, varlıkları kontrol edilir; betik, git, DB, pytest, tarayıcı veya HTTP yok; hiçbir kayıt güncellenmez; önkoşullar `NOT_CHECKED (DRY_RUN)`.

- **Yüklenecek talimatlar:** `references/access-and-web.md`, `references/recovery-verification.md`; AGENTS.md §13, §17–19, §24; docs/05-ARCHITECTURE.md §29–30; SCENARIO-MATRIX §6.
- **Gerçek koşuda kullanılacak kaynaklar:** sentetik kullanıcılarla sandbox veya pytest (ayrılmış test DB), `manage.py check --deploy` (salt okuma), salt okunur `pg_roles`/`has_table_privilege` sorguları, izole restore drill (ayrı DB + geçici medya).
- **Bilinen engeller:** ENV-001, izole restore DB'si yok, üretim/pilot için yazılı onay yok.

## Önkoşullar

- Yetkisiz erişim denemeleri yalnız sandbox (`:8001`, izole test DB) veya pytest (ayrılmış test DB, runbook §4) üzerinde, sentetik kullanıcılarla. Dev sunucusunda yalnız anonim/yetkisiz GET ile yanıt kodu gözlemi.
- Kurtarma denemesi yalnız izole ortamda (ayrı DB + ayrı medya klasörü). **Üretim/pilot ortamında kurtarma denemesi ayrı yazılı onay gerektirir**; onay yoksa PENDING.
- `check --deploy` yalnız okuma: `manage.py check --deploy` mevcut ayarlarla çalıştırılır; ayar dosyası değiştirilmez.

## Yasaklar

Runbook §3'e ek olarak: sır, parola, oturum anahtarı, `.credentials.json` veya ortam değişkeni **değerlerini** okumak/raporlamak (yalnız ad ve var/yok); gerçek kullanıcı hesabının parolasını değiştirmek; Group'lara izin eklemek (`setup_roles` non-destructive kuralı); PostgreSQL rol/yetki değiştirmek (`GRANT/REVOKE/ALTER ROLE`); zararlı dosya yüklemek; dış servise veri göndermek; brute force/DoS.

## Yöntem

1. **Yüzey envanteri:** `*/urls.py` → her rota için yöntem, izin (`permission_required` / servis kontrolü), nesne kapsamı.
2. **Rol matrisi:** TECHNICIAN / STOREKEEPER / ADMIN_MANAGER benzeri sentetik kullanıcılarla her rotaya GET/POST; beklenen = `AGENTS.md` §13 tablosu.
3. **Nesne düzeyi:** başka kullanıcının düzeltme talebi/kanıtı, sayım oturumu, tekil varlık, etiket görüntüsü; UUID tahmin/enumeration; tarama çözümleyicisinin hedef bilgisi sızdırmaması.
4. **Web:** CSRF (POST ve HTMX), güvenli cookie ayarları, `check --deploy`, başlıklar (nosniff, cache-control kanıt dosyalarında).
5. **Sırlar:** `git log -p` ve ağaçta sır desenleri (değer yazmadan konum raporu), `.gitignore` kapsamı.
6. **PostgreSQL:** uygulama rolünün yetkileri (`pg_roles`, `has_table_privilege`, trigger sahipliği) salt okunur sorgularla.
7. **Kurtarma:** `references/recovery-verification.md` (izole drill).
8. **Kayıt:** SEC- önekli bulgular; istismar ayrıntısı yalnız yeniden üretmeye yetecek kadar.

## Kanıt formatı

`[SEC-02 | rol: TECHNICIAN (izinler: …) | yöntem+rota | beklenen 403 | gerçekleşen … | sandbox/LAB-SIM] kanıt yolu`

## Hüküm

- **PASS:** her rol/rota/nesne için beklenen sonuç kanıtlı; kurtarma drill'inde ledger bütünlüğü + projeksiyon + ek→dosya kontrolleri geçti.
- **FAIL:** yetkisiz görme/değiştirme, CSRF'siz durum değişikliği, sır sızıntısı, kurtarmada veri/dosya kaybı.
- **PENDING:** izole ortam, onay veya üretim altyapısı (`DEC-IT-*`) yok.

## Rapor

Runbook §8 + rol × rota matrisi + kurtarma drill tablosu (`adım | süre | sonuç | kanıt`).

## Kapsam dışı

Üretim sızma testi, ağ/OS sertleştirme, IT'nin dağıtım kararları (`DEC-IT-*`), kod düzeltmesi, izin politikasını değiştirme.
