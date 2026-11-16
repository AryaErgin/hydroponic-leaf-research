# iPhone ile adım adım Leaf Research

Her görüntü gerçek uygulamanın 390 piksel genişliğindeki telefon görünümüdür. Görüntüye dokunarak büyütün. Örnekler yalnızca yapay bir bitki ve yerel deneme hesabıyla hazırlanmıştır; araştırma verisi değildir. Safari'nin Photo Library menüsü ve iCloud ekranı iOS'a göre değişir; burada uygulamanın kendi ekranları gösterilir.

## 1. Sign in — giriş yapın

Kendi e-posta adresinizi (1) ve şifrenizi (2) yazın; **Sign in** (3) düğmesine dokunun. İstenirse e-postanızı doğrulayın; kurum erişimi için yönetici onayı da gerekir.

![Telefon ekranında boş Email ve Password alanları ile Sign in düğmesinin işaretli konumları](../src/webapp/static/tutorial/step-01.webp)

## 2. Bitkiyi seçin veya ekleyin

**Plant** (1) listesinden bitkinizi seçin; yeni bitki için **+ Add plant** (2) ile adını ve türünü girip **Add plant** düğmesine dokunun.

![Telefon görünümünde Plant listesi ve Add plant düğmesi işaretli](../src/webapp/static/tutorial/step-02.webp)

## 3. Photo Library'den fotoğraf seçin

İşaretli **Take a photo or choose JPEG, PNG or HEIC** alanına dokunun; iPhone menüsünden **Photo Library** ve fotoğrafınızı seçin. Saksının dış kenarı tam görünür, net ve tepeden çekilmiş bir JPEG, PNG veya HEIC/HEIF fotoğraf kullanın; ölçüm tarihini kontrol edin.

![Telefonun gerçek fotoğraf seçme alanı Photo Library seçimini başlatmak için işaretli](../src/webapp/static/tutorial/step-03.webp)

## 4. Saksı ölçeğini kaydedin

**4a — Çap:** Cetvelle ölçtüğünüz saksı DIŞ çapını santimetre olarak **Pot outer diameter (cm)** (1) alanına yazın. Görseldeki 12 cm yalnızca örnektir.

![Pot outer diameter alanına örnek 12 cm girilmiş gerçek telefon kalibrasyon ekranı](../src/webapp/static/tutorial/step-04.webp)

**4b — İlk nokta:** Fotoğrafta saksının dış kenarındaki ilk noktaya dokunun (2); yaprak sınırını veya iç kenarı seçmeyin.

![Saksının dış kenarında ilk işaretçinin ve karşı kenarı seçme isteminin göründüğü telefon ekranı](../src/webapp/static/tutorial/step-05.webp)

**4c — Karşı nokta:** Merkezden geçen çapın karşı DIŞ kenarına dokunun (3); iki işaretçi arasındaki çizgiyi kontrol edin.

![Saksının iki karşı dış kenarındaki işaretçiler ile aralarındaki çap çizgisinin gerçek telefon görünümü](../src/webapp/static/tutorial/step-06.webp)

**4d — Kaydet:** Aşağı kaydırıp **Save calibration** (4) düğmesine dokunun. İşaretçiyi sürükleyerek veya **Undo last point** ile düzeltebilirsiniz.

![Kalibrasyon açıklamaları ve işaretli Save calibration düğmesini gösteren telefon ekranı](../src/webapp/static/tutorial/step-07.webp)

Eğik, gizli veya kırpılmış saksı kenarıyla ölçek kurmayın; fotoğrafı yeniden çekin. Kamera konumu, yakınlaştırma, kadraj veya saksı değiştiğinde yeniden kalibre edin. Ölçek doğrulanamıyorsa **Review leaves first (pixels only)** ile yaprakları inceleyip kalibrasyonu sonra tamamlayabilirsiniz; önceki onaylı ölçümler değişmez.

## 5. Analyze photo — analizi bekleyin

**Ready to analyze** durumunda **Analyze photo** (1) düğmesine dokunun; **Analyzing** sırasında sayfayı açık tutun. Sunucu ilk kez açılıyorsa birkaç dakika sürebilir; bu, fotoğraf hazırlama hatasından ayrıdır.

![Hazır fotoğraf ve işaretli Analyze photo düğmesinin gerçek telefon görünümü](../src/webapp/static/tutorial/step-08.webp)

## 6. Renkli yaprakları kontrol edin

**Edit tools** (1) ile düzeltme araçlarını açın; **Paint lamina** ile eksik yaprak dokusunu ekleyin, **Erase tissue** ile sapı ve arka planı silin.

![Telefon ekranında işaretli Edit tools düğmesi ve açık gerçek düzeltme araçları](../src/webapp/static/tutorial/step-09.webp)

Fotoğrafı renkli yaprak maskesiyle (2) karşılaştırın; soluk ve sarı yaprak dokusunu koruyun, görünmeyen dokuyu boyamayın. Bittiğinde **Done** düğmesine dokunun.

![Gerçek SegFormer analizi sonrası fotoğraf üzerindeki renkli numaralı yaprak maskesinin telefon ekranı](../src/webapp/static/tutorial/step-10.webp)

- **Split:** Bir bölgede iki yaprak varsa bölgeyi seçip tam kesen bir çizgi çizin.
- **Merge:** Aynı yaprağın parçalarını seçip **Merge selected leaves** ile birleştirin.
- **Reassign:** Alıcı yaprağı seçip aktarılacak pikselleri boyayın.
- **Add leaf:** Atlanan görünür yaprağı boyayarak yeni kimlik verin.
- **Delete leaf:** Yaprak kimliğini kaldırır; kalan dokuyu yeniden atayın veya gerçekten yaprak değilse silin.
- **Undo / Redo:** Düzeltmeyi geri alın veya yineleyin; iki parmakla fotoğrafı taşıyın ve yakınlaştırın.
- **Save draft:** Onaylamadan ara kaydı saklar; geri döndüğünüzde aynı bitkiyi seçip **Resume unfinished review** düğmesine dokunun.

## 7. Ölçümü onaylayın

Yaprak sayısını, alanları ve ölçeği kontrol edip inceleme kutusunu (1) işaretleyin; **Approve measurement** (2) düğmesine dokunun. Düğme kapalıysa üzerindeki eksik-adım açıklamasını tamamlayın; insan onayı zorunludur.

![Telefon ekranında işaretli inceleme onay kutusu ve etkin Approve measurement düğmesi](../src/webapp/static/tutorial/step-11.webp)

## 8. Büyüme geçmişi ve CSV

Bitkinin onaylı **Growth history** bölümünde **Graph metric** (2) ile grafik ölçütünü seçin; **Export CSV** (1) ile geçmişi indirin. Aynı ölçüm hedefini ve tutarlı çekim koşullarını karşılaştırın.

![Gerçek telefon görünümünde Growth history başlığı, Export CSV düğmesi ve Graph metric listesi işaretli](../src/webapp/static/tutorial/step-12.webp)

## Alanın anlamı

Fotoğraf → otomatik yaprak önerisi → insan kontrolü → piksel sayımı → doğrulanmış kalibrasyon → onaylı büyüme geçmişi.

İncelenen piksel sayısı × (santimetre/piksel)², saksı kenarı düzlemindeki yaklaşık izdüşüm alanını verir. Doğrulanmış ölçek yoksa alan piksel olarak kalır. Bu gerçek 3B yaprak yüzey alanı değildir; örtüşen veya gizli doku tek fotoğraftan ölçülemez. Otomatik maske ground truth değildir; deneyiniz için bağımsız gerçek ölçümlerle doğrulayın.

## Sorun olursa

- **Fotoğraf hazırlanamadı:** Ekrandaki nedeni okuyun; fotoğraf seçili kaldıysa **Try again** kullanın veya fotoğraf seçme alanına dokunarak başka fotoğraf seçin. Bitkiyi yeniden oluşturmanız gerekmez. HEIC açılamıyorsa fotoğrafı JPEG olarak dışa aktarıp seçin.
- **Analiz uzun sürüyor:** İlk sunucu açılışını bekleyin; hata sonrası **Resume unfinished review** ile tamamlanıp tamamlanmadığını kontrol edin.
- **Bağlantı kesildi:** Bağlantıyı düzeltip ekrandaki yeniden deneme eylemini kullanın; sayfayı yenilemeden başka fotoğraf seçebilirsiniz.
- **Oturum sona erdi:** Yeniden giriş yapın, aynı bitkiyi seçin ve kaydedilmiş incelemeyi açın; henüz sunucuya ulaşmamış bir fotoğrafı yeniden seçmeniz gerekebilir.
- **Kalibrasyon eksik:** DIŞ çapı ve karşı DIŞ kenar noktalarını kontrol edin; yaprak incelemesi ile ölçek doğrulaması ayrı adımlardır.
- **Onay kaydedilemedi:** Taslağı saklayın; tekrar onaylamadan önce geçmişte tamamlanmış ölçüm olup olmadığını kontrol edin.
