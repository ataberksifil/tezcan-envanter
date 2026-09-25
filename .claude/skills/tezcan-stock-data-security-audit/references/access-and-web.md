# Erişim ve web güvenliği kontrol listesi

## Rol ve izin (AGENTS §13)

- TECHNICIAN: stok görme, çıkış, düzeltme talebi; mal kabul, düzeltme onayı, iade, transfer yok.
- STOREKEEPER: görme, malzeme oluşturma, mal kabul, çıkış, iade, transfer; malzeme değiştirme yok.
- ADMIN_MANAGER: yönetim, master data, düzeltme onayı; sayım farkı onayı ve baseline kuruluşu hassas izinlerdir.
- Gizli/pasif düğme güvenlik değildir: her POST/HTMX uç noktası sunucuda izin kontrol etmeli.
- `setup_roles` mevcut Group'lara sessizce izin eklemez.

## Nesne düzeyi / IDOR

- Düzeltme kanıtı yalnız `/corrections/evidence/<uuid>/` ve `corrections.view_correctionrequest` ile; public `MEDIA_URL` yolu yok.
- Talep sahibi kendi düzeltmesini, sayan kendi farkını onaylayamaz (backend kuralı; burada atlatma denemesi).
- Etiket/QR görüntü uç noktaları ilgili görme izni ister; `Cache-Control: private, no-store`.
- Tarama çözümleyicisi: kimlik doğrulaması zorunlu; yetkisiz türde 403 ve hedef hakkında bilgi yok; bilinmeyen kod durum değiştirmez.
- HTMX salt okunur parçalar (`stock-sources`, `location-stock-contents`): izin yoksa içerik yok, yazma yok, POST 405.

## CSRF ve oturum

- Tüm durum değiştiren istekler CSRF token'lı (HTMX `hx-headers` X-CSRFToken).
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SECURE_*` ayarları üretim profilinde; `DEBUG=False`; `ALLOWED_HOSTS`; `SECRET_KEY` ortamdan (`config/settings.py` `resolve_secret_key`).
- `manage.py check --deploy` çıktısı: yalnız uyarı kimlikleri ve dosya konumu raporlanır.

## Sırlar

- Git geçmişinde parola/anahtar deseni taraması (değer yazmadan: dosya, commit, desen türü).
- DB bağlantısı `POSTGRES_*` ortam değişkenleri (Windows kullanıcı ortamı); projede `.env` yok, `.env.example` var.
- Log'larda parola/çerez/kanıt ikilisi yok (`AGENTS.md` §19).

## Dosya yükleme (düzeltme kanıtı, `DEC-034`)

- Yalnız JPEG/PNG/WebP, içerik doğrulaması, ≤10 MiB, rastgele depolama anahtarı, çalıştırılabilir yükleme yok, HEIC reddi.

## PostgreSQL

- Uygulama rolü süper kullanıcı değil; `CREATEDB` yok (gözlendi); trigger'ları devre dışı bırakamamalı (`ALTER TABLE … DISABLE TRIGGER` yetkisi tablo sahipliğine bağlıdır — sahiplik incelenir, deneme yapılmaz).

## Excel

- Export: `=`, `+`, `-`, `@` ile başlayan kullanıcı hücreleri nötralize. Import: boyut/satır/sayfa sınırları, salt okunur parse.
