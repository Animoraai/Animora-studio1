# Animora v9 — API bağlantıları

## Kurulum

Python 3.10+ ile:

```bash
python -m pip install -r requirements.txt
```

`.env.example` dosyasındaki değişkenleri **sunucunun ortamına** tanımlayın. `.env` dosyası otomatik okunmaz. Gerçek anahtarları HTML içine koymayın.

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

Tarayıcı: http://127.0.0.1:8000

## Bağlantılar

Metin: Gemini → Groq → Cerebras → OpenRouter → Mistral (anahtar tanımlananlar).

Görsel: Hugging Face Inference Providers, `HF_TOKEN` ve model seçimi gerekir. Faturalandırma riskinden dolayı `ENABLE_IMAGE_API=false` varsayılandır. Hesabınızın ücretsiz kotasını ve ödeme ayarlarını doğruladıktan sonra `true` yapın. Bazı modeller ücretli olabilir veya kullanılamayabilir.

Görsel referansları henüz model girdisi değildir: yalnızca karakterlerin yazılı özellikleri prompt'a eklenir. Gerçek yüz tutarlılığı ve video üretimi henüz uygulanmamıştır.

## Test

`python -m compileall -q server.py`

`/api/health` ile anahtarların tanımlı olup olmadığını (anahtarların kendisini değil) kontrol edin. Uygulamada kayıt/giriş yapın, storyboard sahnesi açın, görsel açıklaması oluşturun ve `Gerçek Sahne Görseli Üret` düğmesini deneyin.

Bu demo genel kullanıma açılmadan önce üretim ortamı güvenliği, oran sınırlama, kalıcı bulut depolama ve ödeme altyapısı gerektirir.
