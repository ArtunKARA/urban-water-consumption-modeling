# hihAigua: günlük hedefin belirlenebilirliği

**5 Kasım 2026.** Saat sınırlarına tüketim dağıtmadan ve hacim birimini varsaymadan
günlük hedef çıkarımı kodlandı, gerçek dosyalarda çalıştırıldı. Bu bir model eğitimi değildir.

## Sonuç

2025'in **UTC takviminde**, her bileşen için 10 hane × 365 gün incelendi:

| Bileşen | Kayıtlı endekse göre tek değer | Yalnız alt–üst sınır | Çözülemeyen | Toplam |
|---|---:|---:|---:|---:|
| `water` | 3.184 (%87,23) | 168 | 298 | 3.650 |
| `hotWater` | 3.310 (%90,68) | 40 | 300 | 3.650 |

7.300 satır **bileşen-gün** var; bunlar 7.300 bağımsız hane-günü değil.
Soğuk ve sıcak su ayrı kalır. Bu oranlar günlük endeks farkının belirlenebilirliğini
gösterir. Fiziksel doğruluk, nüfus temsiliyeti veya model başarısı anlamına gelmez.
Hane bazında tek değer oranları soğuk suda %77,8–92,3; sıcak suda %78,4–95,1.

## Sınır hesabı

Kullanılabilir kesitte endeksin sürekli ve azalmayan olduğu; kayıtsız reset,
taşma, cihaz veya ölçek değişimi bulunmadığı varsayılır. Takvim sınırından hemen
önce ve sonra aynı endeks varsa sınır değeri bu gösterimde nettir. Endeks
artmışsa sınır değeri iki okuma arasında kalır.

Başlangıç `[S_alt, S_üst]`, bitiş `[E_alt, E_üst]` olduğunda günlük hacim:

```text
alt sınır = max(0, E_alt - S_üst)
üst sınır = E_üst - S_alt
```

Başlangıç `[10,12]`, bitiş `[20,23]` örneğinde hedef `[8,13]` kaynak birimidir.
Tek hedef **null** kalır; 10,5 gibi bir orta değer atanmaz. İki sınır eşitse ve
kullanılan kayıtlarda engelleyici sorun yoksa tek hedef oluşur. Gerçek sıfır
farkı da geçerli hedef olarak korunur.

Kesinlik kayıtlı endeks gösterimine bağlıdır. Kalibrasyon doğruluğu veya okunmayan
aralıkta gizli reset olmadığını veri tek başına kanıtlamaz. Varsayımlar
[deney politikasında](../research/experiments/hihaigua-counter-days-v1.json) açıktır.
Bu hesapta kaynak ondalıkları yuvarlanmadı.

## Boşluk ve kalite kuralları

- Sınırın bir tarafında kayıt yoksa dışa doğru tahmin yapılmaz.
- Sınırı çevreleyen okuma aralığı 630 saniyeyi aşarsa gün çözümsüz kalır.
- Kullanılan kanıt aralığında eksik/geçersiz endeks, saat çelişkisi veya gözlenen
  endeks düşüşü varsa hedef verilmez.
- Gün içindeki örnekleme boşluğu, kümülatif farkın mutlaka kaybolduğu anlamına
  gelmez. İki kullanılabilir sınır arasındaki toplam, belirtilen süreklilik
  varsayımı altında korunabilir. Boşluk sayısı ayrıca tutulur; ara örnek uydurulmaz.

Tek değere indirgenen soğuk su günlerinin **1.110'unda**, sıcak su günlerinin
**1.164'ünde** kullanılan kayıtlarda örnekleme boşluğu da var. Bu günler eksiksiz
saatlik tüketim eğrisi olarak sunulmaz.

Çözümsüzlükte baskın neden sınır çevresindeki uzun boşluklar. İlk ve son günler
için yeterli dış sınır kaydı yok. Sıcak sudaki bir eksik değer ve bir endeks düşüşü
ayrıca etkili. Bir gün birden fazla nedenle işaretlenebilir; neden sayıları
toplanarak gün sayısı hesaplanmaz.

## Hedefin bilgi zamanı

Gün 00:00'da bitip gereken sonraki okuma 00:04'te geliyorsa hedef 00:00'da hazır
sayılmaz. `label_available_at_sample_clock` gereken gün-sonu sağ okumasını tutar.
Çözümlenebilen hedeflerde gün sonundan bu örneğe kadar medyan süre:

- `water`: **212,45 saniye**, en büyük 420,128 saniye.
- `hotWater`: **214,58 saniye**, en büyük 420,016 saniye.

Bunlar örnek saatinden hesaplanan süreler; sağlayıcının ölçülmüş iletim gecikmesi
değil. Gerçek 2026 arşiv alımı ayrı `archive_received_at` alanında kalır. Örnek
saatine dayalı erişim kullanılacaksa ayrı deney varsayımı gerekir. Eğitim ve
özellik üretimi, gereken kayıtları `as_of` öncesinde erişilebilir oldukları
gösterilmeden kullanmamalı.

## Model kararları ve sınırlamalar

Günlük hedef için bütün saatleri oransal dağıtımla doldurmak tek yol değil.
Artık hangi günün tek değer, hangisinin aralık verdiğini ölçebiliyoruz.

**Yalnız tek değerli günleri seçmek yanlılık yaratabilir:** sınırlarda su kullanan
haneler veya kapsaması farklı dönemler daha az temsil edilebilir. Bu durum gün
sonunda anlaşılır; tahmin anında gelecekteki kesinliğe göre hizmet verilecek gün
seçilemez. Sonraki F01 protokolü kapsamı, dışlanan günleri, hane/zaman dağılımını
ve aralıklı hedeflerin nasıl değerlendirileceğini ayrıca belirtmeli.

UTC açık bir referans takvimidir. İspanyol hanelerin doğrulanmış yerel takvimi
veya tezin `Europe/Istanbul` takvimi diye sunulmadı. Algoritmanın 23/25 saatlik
günleri desteklemesi, veri setinin yerel takviminin doğrulandığı anlamına gelmez.

Hacim birimi halen doğrulanmadığı için **litre dönüşümü, kanonik yayın ve model
eğitimi kapalı**. Bu oturumda yayınevi/Zenodo uçları yeni teknik belgeyi getiremedi;
yerel doğrulanmış arşivle devam edildi. Birim kararı değiştirilmedi.

## Kod, dosyalar ve çalıştırma

- `packages/research/src/urban_water_research/counter_days.py`: kaynaktan bağımsız
  örnek/zaman/endeks hesabı ve dondurulmuş koşum denetimi.
- `scripts/prepare_hihaigua_days.py`: hazırlanmış sayaç kayıtlarını bu örneklere eşler;
  model eğitmez, DB'ye yazmaz.
- [Deney politikası](../research/experiments/hihaigua-counter-days-v1.json).
- [Sayısal sonuçlar](../research/experiments/results/hihaigua-counter-days-v1.json):
  hane sayımları, bilgi zamanı, kaynak/kod hash'leri ve koşum konumu.
- [Notebook](../notebooks/05_hihaigua_counter_days.ipynb): örnek tek değer/aralık/
  çözümsüz günler ve renkli hane karşılaştırması.

```powershell
.\.venv\Scripts\python.exe scripts/prepare_hihaigua_days.py
```

`var/research/hihaigua/v2/counter-days/latest.json` son başarılı koşumu gösterir.
Her koşumda iki Parquet, politika ve kaynak-manifest kopyası, PNG/SVG grafik ve
hash'li manifest bulunur. `verify_counter_day_run()` altı dosyayı, 7.300 satırı ve
kapalı eğitim bayraklarını denetler. Eski koşumların üzerine yazılmaz.

Ondalık/zaman alanlarının satır satır Arrow scalar dönüşümü yavaştı. Kesin ondalık
metinleri ve UTC epoch değerleri sütun halinde okunarak düzeltildi. Eski ve yeni
okuyucunun ürettiği **iki günlük Parquet'in SHA-256 değerleri aynı**; hız için
sayısal hassasiyetten vazgeçilmedi.

Testler sıfır/eksik ayrımı, sınır belirsizliği, reset, bilgi zamanı, gelecek kayıt
değişikliği, yaz saati/atlanan gün, hassasiyet, dosya bozulması ve eğitim kapısını
sınar. Gerçek veri sayıları kontrollü sınama örneklerinden ayrı tutulur.
[Önceki kaynak/adaptör raporu](hihaigua-reference.md) temel kanıtları açıklar.

Bu teslimatta **301 test geçti, atlanan test yok**. Yeni günlük sayaç katmanının
36 testi ve mevcut sistemin regresyonları birlikte çalıştırıldı. Notebook'un
üç kod hücresi hatasız yürütüldü. Altı koşum dosyası ve 7.300 satır doğrulandı;
özet [doğrulama kaydında](../research/experiments/results/hihaigua-counter-days-validation.v1.json).
