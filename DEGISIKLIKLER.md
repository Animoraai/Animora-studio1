# Animora onarım notları

- Proje oluşturma sırasında tarayıcı depolaması başarısız olursa sahte başarı gösterilmez.
- AI storyboard yanıtlarında HTTP hata mesajları düzgün okunur.
- AI storyboard 503 servis hatası yanlışlıkla 502 JSON hatasına dönüştürülmez.
- AI anahtarı eksik olduğunda Render yapılandırmasına yönelik açıklama gösterilir.
- AI senaryo işleminde global fetch fonksiyonunun geçici değiştirilmesi kaldırıldı.
- AI gerektirmeyen proje, bölüm, karakter ve manuel sahne işlemleri korunmuştur.

## Önemli sınırlamalar
- Gerçek video üretim API'si bu pakette mevcut değildir.
- Görsel üretimi ücret/kota kontrolü nedeniyle varsayılan kapalıdır.
- Projeler tarayıcı localStorage alanında saklanır, sunucuya eşitlenmez.
- Gerçek AI üretimi için Render ortamında geçerli GEMINI_API_KEY veya diğer sağlayıcı anahtarı gereklidir.
- Render Free üzerindeki SQLite kullanıcı veritabanı kalıcı olmayabilir.
