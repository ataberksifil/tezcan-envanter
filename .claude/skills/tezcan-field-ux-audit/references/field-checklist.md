# Saha UX kontrol listesi

Her madde bir kanıt sorusudur. Cevap gözlemle verilir; "muhtemelen" yazılmaz.

## A. Günlük dört işlem (UX-F-02…04, UX-SG-03/05/06/12/13)

- Ana sayfadan her işleme tek dokunuşla ulaşılıyor mu (yetkiye göre)? Sıra: Mevcut Stok, Mal Kabul, Stok Çıkışı, Yerleştir / Transfer Et.
- Çıkışta önce göz (TZ1L) okutulunca: tek kayıtlı gözde malzeme+kondisyon doluyor mu; çoklu gözde açık seçim isteniyor mu; boş gözde "stok yok" görünüyor ve önceki otomatik dolum temizleniyor mu?
- TZ1M okutmanın fiziksel kaynağı doğrulamadığı ekranda söyleniyor mu?
- Kayıt hiçbir zaman otomatik gönderiliyor mu? (Gönderilmemeli.)
- Çıkışta alıcı, hat, kullanım yeri zorunlu alanları anlaşılır mı; hata sonrası girilen veri korunuyor mu?
- Mal Kabul'de terim tek mi ("Kabul konumu"); adetli önizleme → onay; tekil kayıt → etiket bağlantısı.
- Başarı/tekrar gönderim mesajları ("kaydedildi" / "daha önce kaydedilmişti") açık mı?

## B. Okutma ve kimlik (UX-SG-20/21, UX-F-06, DEV-xx)

- Her okutma alanı beklenen türü söylüyor mu (TZ1M/TZ1L/TZ1A)? Yanlış tür, bilinmeyen kod, EAN için ayrı ve Türkçe mesaj.
- HID Enter formu göndermiyor, sonraki alana geçiyor mu?
- Hatalı okutmadan sonra alan seçili kalıyor mu (sonraki okutma üzerine yazar)?
- Yetkisiz okutmada hedef hakkında bilgi sızmadan açık mesaj.
- Etiket sayfası: "oluşturuldu ≠ basıldı/yapıştırıldı" notu; yeniden basım aynı kimlik.

## C. Düzen ve cihaz

- 1440/1280/1024/768/390 px'te yatay taşma yok; tablo → kart dönüşümü okunur.
- Dokunma hedefi ≥ 44 px (mobil); sabit alt çubuk/aksiyon çubuğu son alanı veya kaydet düğmesini örtmüyor.
- Masaüstü gezinme: **UI-001** — dar ikon sütunu, kopuk daraltma düğmesi, gereksiz boş alan sahip tarafından reddedildi. Yalnız A (UI-001 incelemesi) kanıtlar ve kaydına ekler; çözüm yalnız PROPOSAL. B (bütün-ekran denetimi) bu maddeyi yeniden denetlemez, UI-001'e atıf yapar (SKILL.md "İki ayrı çalışma").
- Formlar okunabilir genişlikte; listeler/panolar genişliği kullanıyor.

## D. Erişilebilirlik

- Tüm etkileşimler klavyeyle; odak halkası görünür; Escape ile menü kapanır.
- Her alanın `label`'ı; hata metni alanla ilişkili; `aria-live` duyuruları.
- Durum yalnız renkle anlatılmıyor (etiket metni var).
- Kontrast: gövde metni ≥ 4.5:1.

## E. Metin ve anlam

- Kullanıcı metni Türkçe; miktar biçimi `80`, `2,5`, `1.250,75` (`|qty`).
- Aynı kavram aynı kelime (konum/göz/raf; kondisyon adları).
- Minimum stok: "sayılan stok" ve "bozuk/sayılmayan" ayrımı `DEC-042` ile tutarlı; SKT sınırı yazılı.
