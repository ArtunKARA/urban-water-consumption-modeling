# Veri ve model akış haritası denetimi

Tarih: 27 Ekim 2026. Kapsam: araştırma JSON sözleşmesi, model planı, üretici
scriptler, seçilebilir görünüm ve toplu SVG/PNG/PDF. Bu rapor çalışan sağlayıcı
adaptörü, model başarısı veya veritabanı altyapısı için yeni bir kabul kanıtı değildir.

## Bulunan ve düzeltilen noktalar

| Kimlik | Karşı örnek / sorun | Düzeltme ve kalıcı kontrol |
|---|---|---|
| MF01 | D07 bildirimi türetilmiş bir alan üzerinden `feature` yapılınca eski kontrol kabul ediyordu. Onay alanı veya başka adla kopyası da öğrenme girdisi olabiliyordu. | Kaynak şema anlamına göre rol kontrolü; türetme ataları boyunca bildirim/onay sızıntısını reddetme. Çok adımlı türetme dahil olumsuz regresyonlar eklendi. |
| MF02 | H01 alanı G01 şema yoluna bağlanınca, kaynağı yanlış seçilince veya `derived_fom` gibi yazım hatası eklenince eski kontrol kabul ediyordu. | 14 güncel soru için tam eşleşme, kaynak–şema tanımı eşleşmesi, kapalı özellik kümeleri ve hava değişkeni seçicisi kontrolü. |
| MF03 | JSON'da yinelenen anahtarlar sessizce kaybolabilir; `odd:id` gibi kimlikler ekran seçicilerini bozabilir; boş yöntem bilgileri kabul edilebilirdi. | Yinelenen anahtar ve NaN/Infinity reddi, güvenli kimlikler ve zorunlu metin/tür kontrolleri. |
| MF04 | A01'in kendi çıktısını girdi alması kabul ediliyordu. Toplu çizim ayrıca A01/V01 adlarını ve katmanlarını kod içine sabitlemişti. | Açık `producer_model`, kaynak türü, çıktı ailesi ve bağımlılık döngüsü kontrolleri. Katmanlar bağımlılıktan hesaplanır; model sırası ve adı değişimi test edilir. |
| MF05 | PNG/PDF içeriği değiştirilse bile eski denetim yalnız dosya varlığı ve SVG özetine bakıyordu. Manifest silinince kontrol atlanabiliyordu. | PNG/PDF'nin ayrı SHA256 ve byte boyu; eksik manifest/dosya reddi. Sözleşme kimliği de üretilen görünüm ve SVG'ye bağlandı. |
| MF06 | A01 akışı skor üretiyor ama Anomaly nesnesinde skor alanı yoktu; `additionalProperties=false` nedeniyle skor saklanamıyordu. | `score`, `score_method`, `score_direction`; sonuç yoksa null, hazır sonuçta yöntem/yön/sürüm zorunlu. Kanıtı olmayan confirmed durumuna izin verilmez. |
| MF07 | F03 aynı dönemli bölgesel çıktı planlıyor ama tahmin/değerlendirme ufku yalnız günlük/saatlik iki seçenek kabul ediyordu. | `regional_period` eklendi; bölgesel hedef `area` olmalı. Değerlendirme görevi ile ufkun tutarlılığı denetlenir. |
| MF08 | Hava erişim kontrolü geçmiş modeli için de zorunluymuş gibi okunabiliyordu. Kontrolü yanlış varyanta taşımak ayırt edilmiyordu. | Temel/isteğe bağlı/koşullu kontroller ve varyantlar ayrıldı. Operasyonel hava bağlamı doğru yayım/erişim ve bölge kontrollerine bağlanır; senaryo açıkça farklıdır. |
| MF09 | Sayaç hatasının referans hacimle mi ölçülen hacimle mi ağırlıklanacağı açık değildi; debi ekseni ve hata tanımı da saklanamıyordu. | Referans hacim temeli, dönüşüm sınırları ve eğrinin `flow_basis` / `error_definition` alanları açıklandı. Hazır sonuçta bilinmeyen temel veya kaynaksız sayısal hata reddedilir. |
| MF10 | Sağlayıcı alım saati ile araştırma sisteminin kaynağa ilk erişimi karışabiliyordu. Bazı sonuç türleri ready/null veya failed/sayısal değer kabul ediyordu. | `Source.known_at` eklendi; eski revision/bağlamın geçmiş tahmine sızmaması koşulu netleştirildi. Profil, sayaç performansı ve değerlendirme durum kuralları sıkılaştırıldı. |

MF01–MF04'ün temel karşı örnekleri düzeltme öncesi bellekte değiştirilmiş plan
kopyalarıyla çalıştırıldı; hatalı durumlar kabul ediliyordu. Yeni testler bu
değişiklikleri reddetmeyi ve geçerli planın çalışmayı sürdürmesini kontrol eder.
Gerçek dosya veya veri üzerinde bozma/silme denemesi yapılmadı; bütün hasar
senaryoları pytest'in geçici klasöründe veya bellekte çalışır.

## Sayaç hata hesabındaki ayrım

İşaretli bağıl hata referans hacme göre tanımlanır:

`e = (V_olculen - V_referans) / V_referans`, yüzde değer `100*e`.
Eğrinin `signed_error_percent` değeri oran hesabında önce 100'e bölünür.
Tanım için [OIML R 49-2:2013, B.3](https://test.oiml.org/en/files/pdf_r/r049-2-e13.pdf)
esas alınmıştır; bu plan bir uygunluk/kalibrasyon sertifikası değildir.

Karşı örnek: iki aralıkta referans hacimler 10 L ve 10 L; hatalar +%100 ve -%50
olsun. Sayaç 20 L ve 5 L gösterir. Toplam hata `(25-20)/20 = %25` olur. Ölçülen
hacimlerle doğrudan hata ağırlıklamak `%70` verir ve yanlıştır. Bu sayılar yöntemi
açıklayan bir karşı örnektir; geçerli bir gerçek sayaç eğrisi oldukları iddia edilmez.

Yalnız ölçülen hacim biliniyorsa `V_referans = V_olculen/(1+e)` ancak uygun
tanım, debi ekseni ve `e > -1` koşuluyla kullanılabilir. Referans debi eksenli
eğriye ölçülen debi doğrudan konulmaz; çözümün geçerliliği ayrıca kanıtlanır.
Sıfır referans toplamında hata oranı üretilmez. Eksik veya eğri dışındaki bölüm
sessizce normalize edilmez; kapsam ve dışlanan hacim sonuçta belirtilir.

## Erişilebilirlik ve veri yeterliliği

- `source_ref.known_at` / `Source.known_at` araştırmanın bilgi erişim zamanını
  temsil eder. Sağlayıcı bir kaydı 10 Ocak'ta almış, araştırma sistemi 20 Ocak'ta
  görmüşse 15 Ocak tahminine girdi yapılamaz. Aynı olay/revision için birden fazla
  teslimat varsa ilk kanıtlı araştırma erişimi kullanılır.
- Tahmin anı, hedef gün/saat ve kullanılan revision seçimi deney manifestinde
  sabitlenir. İçinde bulunulan tamamlanmamış gün tam günlük gecikme değildir.
- Hava kaydı `forecast` ise yayım ve erişim, `observed` ise erişim zamanı kontrol
  edilir. Gözlenmiş hava için sahte `issued_at` yazılmaz. `scenario` bir varsayımdır.
- Girdi dönemi kapsaması ile gelecekte gerçekleşecek hedef dönemin değerlendirme
  uygunluğu ayrılır; gelecekteki cihaz durumu öğrenme özelliği olmaz.

## Tekrar çalıştırma

Bu oturumda **80 hedefli test geçti**; atlanan test yoktu. Bunlar model planı ve
araştırma sözleşmesi regresyonları ile mevcut ölçüm sözleşmesi testleridir.
Ruff ve kilit dosyası kontrolü de geçti. Bütün 7 akış 736/320 pikselde açık/koyu
tema ile tarayıcıdan kontrol edildi. Toplu grafikte 50 alan ve 84 alan–model
bağlantısı doğrulandı; ok uçları, taşan etiket ve çakışan kutu hatası bulunmadı.
PNG/PDF yeniden üretildi ve içerik özetleri doğrulandı. Bu, depodaki bütün
PostgreSQL testlerinin bu oturumda çalıştırıldığı anlamına gelmez.

**Kullanıcı kararları sonrası güncelleme:** 18:00/23:00, iki profil yaklaşımı ve
üç senaryo ailesi plana/protokollere işlendi. Gün içi özellik eklendi. Bu güncel
sürümde **92 hedefli test**, tarayıcı kontrolleri ve 51 alan/7 model/85 bağlantı
dışa aktarma denetimi geçti; yeni PNG/PDF içerik özetleri doğrulandı. Eğitim çalıştırılmadı.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_flow_plan.py tests/test_contracts.py -q
.\.venv\Scripts\ruff.exe check scripts/build_model_flow_map.py scripts/model_flow_validation.py scripts/model_flow_overview.py tests/test_model_flow_plan.py
.\.venv\Scripts\python.exe scripts/build_model_flow_map.py
node scripts/export_model_flow_graph.cjs
node scripts/verify_model_flow_view.cjs
.\.venv\Scripts\python.exe scripts/build_model_flow_map.py --check
```

Tarayıcı komutları isteğe bağlı Playwright ve kurulu Chrome ister. Çalıştırma
sonuçları `var/proofs/model-flow-map-audit/`, dışa aktarma özetleri
[manifestte](model-flow-overview.exports.json) tutulur. Görünüm testleri her
modelde beklenen bütün alan/rolleri, yorum alanlarının eğitimden ayrılmasını,
736/320 pikselde açık/koyu temayı, seçimleri ve hatalı kayıtlı durum girişini kontrol eder.
Toplu grafik denetimi her okun başlangıç/bitişinin ilan edilen kutuya ulaştığını
da doğrular; yalnız etiket isimlerinin eşleşmesiyle yetinmez.

## Kanıtın sınırları ve açık kapılar

- Bu kontroller plan/şema/üretici/görünüm için geçerlidir. Veritabanı ve mevcut
  ingestion/worker akışı bu değişikliklerle yeniden kabul testine sokulmadı.
- Gerçek sağlayıcı örneği, kaynak saatleri, veri kapsaması, hane eşlemesi ve
  referans eğrileri henüz gerçek sahada doğrulanmış değildir.
- Günlük tahmin saatleri sonradan kullanıcı kararıyla 18:00 ve 23:00 olarak
  kesinleşti. [Bu kararlarla güncellenen eğitim planı](model-training-plan.md)
  gün içi özelliği ve deney ayrımlarını kaydeder; güncel harita 51 alan/85 bağlantıdır.
  Eğitim/test tarihleri, kapsama eşikleri ve V01'in durum/geçiş süreleri açık kalır.
- JSON Schema tipleri ve koşulları kontrol eder. Kayıtlar arası yabancı anahtarlar,
  aralık sırası/çakışmaları, bütün 24 saatlik tahmin bloğu ve gerçek as_of seçimi
  çalışma kodunda ayrıca denetlenecektir; bu rapor o kodun yazıldığını iddia etmez.
- Sözleşme yayımlanmamış v0.1 taslağıdır. Skor alanları ve bazı durum koşulları
  sıkılaştırıldığı için eski taslak çıktı örnekleri güncellenmelidir. Çalışan
  `synthetic.measurement.v1` CLI sözleşmesi ve PostgreSQL şeması değiştirilmedi.
- Model eğitimi, anomali başarısı, kuraklıkta nedensel etki veya fiziksel sayaç
  doğruluğu bu kontrollerle kanıtlanmış değildir.
