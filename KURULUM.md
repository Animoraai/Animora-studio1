# Animora Render test kurulumu

1. GitHub hesabında `animora-studio` isimli **Private** depo oluştur.
2. Bu klasörün **içindeki dosyaları** GitHub deposuna yükle (ZIP dosyasını tek dosya olarak yükleme).
3. Render > New > Blueprint yoluyla GitHub deposunu bağla; `render.yaml` dosyası algılanır.
4. Render ortam değişkenlerine GEMINI_API_KEY ve GROQ_API_KEY değerlerini kendi hesabından ekle. Anahtarları GitHub'a yükleme.
5. Deploy tamamlandığında Render'ın verdiği `https://...onrender.com` adresini aç. `/api/health` endpoint'inde yapılandırmayı kontrol et.

**ÖNEMLİ:** Bu yalnızca kişisel test dağıtımıdır. SQLite veritabanı Render ücretsiz dosya sisteminde yeniden dağıtım/yeniden başlatmada kaybolabilir. Kalıcı PostgreSQL, merkezi proje depolama, rate limiting, e-posta doğrulama, içerik güvenliği, ödeme doğrulama ve üretim güvenlik kontrolleri yapılmadan herkese açma. `ENABLE_IMAGE_API=false` bırak; ücretlendirmeyi kontrol etmeden açma.
