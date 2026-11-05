# İlk günlük tahmin deneyi

4 Kasım 2026. F01 için sınırlı, geriye dönük açık veri karşılaştırması çalıştırıldı.
[Aalborg kaynağının](aalborg-reference.md) yayımlanmış işlenmiş serisi kullanıldı.
Ana tez protokolündeki İstanbul saatleri korunur; bu ayrı referans deneyi
`Europe/Copenhagen` takviminde 18:00 ve 23:00 saatlerini karşılaştırır.

## Nereden ayarlanır, nerede çalıştırılır?

- [Deney protokolü](../research/experiments/aalborg-daily-v1.toml): zaman bölmeleri,
  tahmin saatleri, erişim varsayımı, ayrılan haneler ve model parametreleri.
- [Kaydedilmiş sonuç özeti](../research/experiments/results/aalborg-daily-v1.json):
  tüm doğrulama/test ölçüleri, eşlenmiş saat karşılaştırmaları, fold envanteri ve sürümler.
- [İnceleme notebook'u](../notebooks/03_daily_forecast_reference.ipynb): son koşumun
  doğrulanması, ölçüleri ve grafiği.
- `var/experiments/aalborg-daily-v1/latest.json`: son başarılı koşumun konumu.
  Her koşum ayrı dizinde saklanır; eski koşumun üzerine yazılmaz.

Repo kökünde:

```powershell
.\.tools\bin\uv.exe sync --locked --extra research --extra notebooks
.\.venv\Scripts\python.exe scripts/run_daily_forecast.py
```

Girdi varsayılan olarak `var/research/aalborg/v1/import-report.json` içindeki
dondurulmuş saatlik Parquet'tir. `--input`, `--protocol`, `--output-root` seçenekleri
vardır. Girdi snapshot'ının hash'i, satır sayısı, zaman dilimi ve kaynak anlamı
protokolle karşılaştırılır. Deney veri tabanına yazmaz; API/CSV çözümlemesi yapmaz.
Öğrenme bağımlılıkları ayrı `packages/research/` paketindedir; ingestion çekirdeği
scikit-learn veya matplotlib'e bağımlı hale getirilmedi.

## Hedef, örnekler ve bilgi sınırı

Hedef ertesi **yerel takvim gününün** toplam işlenmiş litresidir. Örneğin 8 Kasım
hedefi için 7 Kasım 18:00/23:00'te tahmin yapılır. Son tamamlanmış gün 6 Kasım;
haftalık naif yöntem 1 Kasım toplamını kullanır.

12 temel özellik: son tamamlanan gün, 7/14 gün önceki hedef günü, son 7 günün
ortalama/sapması, son 28 günün ortalaması/sıfır gün oranı ve bilinen takvim alanları.
Gün içi varyantı dört alan daha alır: birikmiş hacim, erişilebilir saat sayısı,
geçen gerçek süre ve bu süreye göre erişilebilir saat oranı. Hane kimliği yalnız
eşleme anahtarıdır; doluluk, anket, hava ve hedef değer özellik değildir.

Saatlik erişim **varsayımdır**: dönem bitişi + gecikme, tahmin anından kesinlikle
önce olmalıdır. Varsayılan gecikme 0 dakikadır; normal bir günde 18:00 için
17 saat, 23:00 için 22 saat görünür. Tam sınırda biten saat dahil edilmez.
DST'de geçen gerçek süre ve saat sayısı UTC üzerinden hesaplanır. Arşivin gerçek
alım zamanı 2026'dır; 2022 çevrimiçi veri erişimi ölçülmüş değildir.

7.300 adaydan 6.680 saat/örnek tutuldu: her saat için 2.130 eğitim bölmesi,
610 doğrulama, 600 test örneği. Kısmi hedef günü nedeniyle 40; 28 tam günlük
geçmiş bulunmadığından 580 örnek dışlandı. Gerçek sıfır hedefler tutuldu;
eksik saatler sıfırla doldurulmadı. İki saatin ortak 3.340 hane/hedef günü var;
bu kaynakta eşlenemeyen örnek yok. Başka girdide eşlenemeyenler ayrıca sayılır.

## Eğitim ve test sınırları

Eğitim bölmesi Ocak–Ağustos, doğrulama Eylül–Ekim, test Kasım–Aralık'tır.
Isınma sonrası ilk uygun hedef 31 Ocak; son uygun hedef 30 Aralık'tır.
Eğitim bölmesinde bulunmak tek başına fit'e girmek için yeterli değildir:
sonucu tahmin kökeninde henüz tamamlanmamış hedefler ayrıca purge edilir.
Örneğin 1 Eylül hedefi öncesindeki ilk fit, 31 Ağustos hedefini kullanamaz.

Test kökenleri protokolde önceden 1 Kasım ve 1 Aralık hedefleri olarak belirlenir.
Aralık fit'i yalnız o zamana kadar tamamlanmış geçmiş Kasım hedeflerini ekleyebilir.
Test skorlarına bakarak parametre değiştirilmedi. Doğrulama ve test ayrı raporlanır.

İkinci değerlendirmede `AALBORG-D-09` ve `AALBORG-D-10` eğitim hedeflerinden bütün
kökenlerde çıkarılır. Bu hanelerin tahmin anına kadar olan kendi tüketim geçmişi
özellik olarak kullanılabilir; geçmişsiz yeni hane başarısı iddia edilmez.

Yöntemler: mevsimsel/haftalık naif; yalnız geçmiş kullanan
[HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html);
geçmiş + gün içi varyantı. Poisson, 100 iterasyon, seed 42 ve
`early_stopping=False` sabittir. İç rastgele doğrulama bölmesi oluşturulmaz.
Her saat/örneklem/köken için ayrı model fit edilir. Toplam 12 fit kökeni, 24
öğrenilmiş model vardır; naif yöntem model fit etmez.

## Sonuçlar

Test MAE, litre/hane-gün; düşük değer daha iyidir:

| Örneklem | Saat | Haftalık naif | Geçmiş | Geçmiş + gün içi |
|---|---:|---:|---:|---:|
| 10 bilinen hane, 600 hedef | 18:00 | 106,51 | 84,90 | 84,77 |
| 10 bilinen hane, 600 hedef | 23:00 | 106,51 | 84,90 | 83,49 |
| Ayrılan 2 hane, 120 hedef | 18:00 | 136,77 | 110,46 | 113,61 |
| Ayrılan 2 hane, 120 hedef | 23:00 | 136,77 | 110,46 | 111,91 |

Bilinen hanelerde 23:00 gün içi varyantının MAE'si haftalık naiften yaklaşık
%21,6 düşüktür. Ayrılan hanelerde gün içi eklemek, yalnız geçmiş modelini
iyileştirmedi. Bu sonuçlar saklandı; olumsuz farklar gizlenmedi.

RMSE, sıfır payda durumu açıklanan WAPE, hane/fold ölçüleri ve ortak örneklerde
18:00/23:00 farkları JSON'dadır. Farklar betimseldir; nüfus genellemesi veya
istatistiksel anlamlılık iddiası yoktur. Tahmin aralığı/kapsaması üretilmedi.

## Kanıt ve sınırlar

225 test geçti; atlanan test yok. Testler gelecek saat/hedef değişikliğinin önceki
özellikleri etkileyememesini, sınır hedeflerinin purge edilmesini, hane holdout'u,
DST'yi, sıfır paydayı, tekrar üretilebilirliği ve dosya bozulmasını sınar. Gerçek
veriyle iki bağımsız koşumun bütün hata ölçüleri birebir aynı çıktı.

Başarılı son koşum: `7e387da7b7fb45c4871ad8b78121837f`. Girdi manifesti, protokol,
özellik/örnek tablosu, dışlamalar, fold anahtarları, tahminler, model dosyaları,
rapor ve grafik hash'lerle kaydedildi. `verify_run()` bütün kayıtlı dosyaları denetler.

Kaynakta interpolasyon ve günlük minimum çıkarımı gelecekteki saatlerden
etkilenebilir. Bu nedenle sayılar yayımlanmış **işlenmiş seri** üzerindeki geriye
dönük deneyin sonucudur. Ham tüketim, gerçek zamanlı kurum hizmeti, doğrulanmış
kaçak, mahalle/kuraklık etkisi veya üç makalenin tamamı için kanıt değildir.
F02 saatlik hedef, bağlamlı modeller, simülatör kalibrasyonu ve diğer araştırma
hatları sonraki teslimatlardır.

4 Kasım sonuç değerlendirmesi ve sonraki dış kohort adayları:
[Açık veri kütüphanesi](public-data-library.md). Naife göre %21,6 farkın,
gün içi bilginin geçmiş modeline göre %1,66 ek katkısından ayrımı burada açıklandı.
