from django import forms


class ResolveForm(forms.Form):
    payload = forms.CharField(
        label="Barkod / QR içeriği",
        max_length=128,
        strip=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control font-monospace",
                "autocomplete": "off",
                "autocapitalize": "off",
                "spellcheck": "false",
                "autofocus": True,
                "placeholder": "TZ1M:AAAAAAAAAAAAAAAAAAAAAA",
            }
        ),
    )


QRResolveForm = ResolveForm
