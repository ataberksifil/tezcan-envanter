from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0014_receipt_expiry"),
    ]

    operations = [
        migrations.AddField(
            model_name="expiryinspection",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("KONTROL_EDILDI_UYGUN", "Kontrol edildi, uygun"),
                    ("URUN_BULUNAMADI", "Ürün bulunamadı"),
                    ("AYIRMA_GEREKIYOR", "Ayırma gerekiyor"),
                    ("SONUC_KAYDEDILMEMIS", "Sonuç kaydedilmemiş (eski kayıt)"),
                ],
                default="SONUC_KAYDEDILMEMIS",
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="expiryinspection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("outcome__in", [
                        "SONUC_KAYDEDILMEMIS",
                        "KONTROL_EDILDI_UYGUN",
                        "URUN_BULUNAMADI",
                        "AYIRMA_GEREKIYOR",
                    ])
                ),
                name="inventory_exp_insp_outcome",
            ),
        ),
    ]
