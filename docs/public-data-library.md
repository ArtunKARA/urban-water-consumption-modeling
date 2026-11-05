# Açık veri kütüphanesi ve araştırma değerlendirmesi

İnceleme tarihi: **4 Kasım 2026**. Birincil yayıncı sayfaları, veri paketleri,
veri sözlükleri ve yayımlanan işleme kodları karşılaştırıldı. Özgün dosyalar
indirildi. Yayımlanan Python/R/notebook kodları çalıştırılmadı.

## Sonuç ve kapsam

Mevcut Aalborg deneyi, tekrar üretilebilir bir ilk karşılaştırma sağlıyor.
Genellenebilir bir tüketim veya kaçak modeli sonucuna henüz ulaşmadık.
Genişletilen kütüphane, farklı ülkelerdeki tüketim serilerini, armatür kayıtlarını,
olay özelliklerini ve bölgesel bağlamı **ayrı kaynaklar olarak** incelemeye hazır.

- **11 katalog kaydı, 56 özgün dosya, 282.261.323 bayt** indirildi ve çevrimdışı
  bütünlük kontrolünden geçti. Katalogda mevcut Aalborg referansı da bulunur.
  Bunlar 11 bağımsız hane örneklemi değildir; aynı araştırmanın farklı ürünleri vardır.
- **68 CSV kaynak tablosu**, toplam **15.951.445 kayıt**, kaynak bazında Parquet'e
  çevrildi. Sayı cihaz kayıtlarını ve yinelenen sonuç temsillerini içerir;
  bağımsız eğitim örneği veya benzersiz hane sayısı değildir.
- Ayrıca **16.436 günlük iklim kaydı** ve **1.448 bölge/dönem kaydı** hazırlandı.
  Bölgesel tabloda örtüşen sürümler ve yayıncı toplamları ayrı tutulur.
- Özgün dosya SHA-256 değerleri, varsa yayıncı MD5 değerleri, URL, gerçek indirme
  zamanı, satır sayıları, sütunlar ve kategori dağılımları saklandı.

Kaynak değerlerini koruyan Parquet katmanı ile modelin kanonik ölçüm tablosu
ayrıdır. Yeni kaynaklar kurum DB'sine veya mevcut eğitim veri setine otomatik
katılmadı. Birim, saat, kapsam ve etiket sorunlarını çözmek için bu katman gereklidir.

## İlk tahmin deneyini nasıl değerlendiriyorum?

Test MAE, litre/hane-gün:

| Örneklem, 23:00 | Haftalık naif | Yalnız geçmiş | Geçmiş + gün içi |
|---|---:|---:|---:|
| Bilinen 10 hane, 600 hedef | 106,51 | 84,90 | 83,49 |
| Eğitimden ayrılan 2 hane, 120 hedef | 136,77 | 110,46 | 111,91 |

**%21,61 iyileşme haftalık naife göredir.** Gün içi verinin geçmiş modeline ek
katkısı yalnız **%1,66**. Ayrılan iki hanede gün içi eklemek hatayı **%1,31
artırıyor**. Bilinen hanelerde WAPE %34,21, ayrılanlarda %51,11. Sonuçların bir
bölümünün olumlu olması operasyonel yeterlilik anlamına gelmez.

On hane ve tek işlenmiş yıl, nüfus genellemesi için dar kalıyor. Ayrılan hanelerin
kendi geçmişi özellik olarak kullanılıyor; bu geçmişsiz yeni hane sınaması değil.
Tahmin aralıkları ve bağımsız dış örneklem sonucu yok. Aalborg'daki interpolasyon
ve günlük minimum çıkarımı, yayımlanmış seriyi gelecekteki kayıtlardan
etkileyebiliyor. Model tarafındaki `as_of` sınırı kaynak ön işlemesindeki bu
sorunu ortadan kaldırmaz. Tarihsel veri erişimi de ölçülmüş değil, varsayımdır.

Bu nedenle yeni bir deneyi, seçilecek dış kohortta **önceden dondurulmuş protokolle**
kurmalıyız. Aalborg test sonuçlarına bakıp ayar değiştirdikten sonra aynı dönemi
yeniden dokunulmamış test gibi sunmamalıyız. Ek mevsimsel/ortalama baselines,
hane bazında hata dağılımı ve zaman bloklarıyla belirsizlik değerlendirmesi yeni
protokolde tanımlanmalı. Günlük hedef ile sonraki 24 saat hedefi yine ayrıdır.

[Deney ayrıntıları](daily-forecast-reference.md) ve
[sayısal kaynak/deney denetimi](../research/reference_data/public-library-audit.v1.json)
bu değerlendirmeyi destekler. Bu adımda yeniden model ayarı veya eğitim yapılmadı.

## İndirilen kaynaklar ve kullanım kararları

| Kaynak | Dosyada doğrulanan kapsam | Bizim için kullanım | Karar / sınırlama |
|---|---|---|---|
| [hihAigua v2](https://zenodo.org/records/21704780) | 10 İspanyol hane, 2025 çoklu hizmet telemetrisi; evlerde yaklaşık 7 dakika. Ayrı laboratuvarda 70 duş, 17 çamaşır, 30 WC etiketi | F01/F02 için ilk dış kohort adayı, tüketim örüntüleri; ayrı laboratuvar olay sınaması | CC BY 4.0. Endeks birimi, saat ve su tesisatı kapsamı doğrulanınca kanonik adaptör |
| [HSB Living Lab v1.1.0](https://zenodo.org/records/22076411) | 652.315 temizlenmiş kayıt; 214 sensör; 28 daire; 10 dakika, 2019–2023 | Armatür örüntüleri ve ileride senaryo dayanakları | CC BY 4.0. Birim çelişkisi ve silinen sıfırlar nedeniyle tam hane serisi olarak alınmaz |
| [CIWS Utah](https://www.hydroshare.org/resource/3143b3b1bdff48e0aaebcb4aedf02feb/) | 5 hanenin 10 mevsimlik pulse dosyası, 3.413.675 kayıt; ayrıca 537 elle etiketli eğitim olayı ve 5 bağlam satırı | Pulse adaptörü, kısa dönem örüntüleri, sınırlı bağlam karşılaştırması | CC BY 4.0. Etiketli eğitim ile otomatik sonuçlar ayrılır; metadata/yayın farkları çözülür |
| [Illinois gerçek olayları](https://stillwell.cee.illinois.edu/data/) | 675 olay özelliği: zaman, süre, ortalama debi, hacim, 6 sınıf | Kullanım türü sınıflandırma referansı | Yayıncı elle doğrulandığını belirtiyor. Kesintisiz 1 saniyelik seri değil. Veri lisansı ayrıca teyit edilmeli |
| [Illinois bağlam ve armatür paketleri](https://stillwell.cee.illinois.edu/data/) | Bağlam çalışmasında 3.963 ve 40.651 olaylık iki görünüm + 124 sıcaklık satırı; 285 duş olayı; 11 kısa armatür izi, 12.494 satır | Normal davranış değişimleri; süre/debi ve olay şekli incelemesi | `Predicted_Label` sonuçları elle doğrulanmış truth sayılmaz. Paketlerin lisansı açıkça doğrulanmadı |
| [Illinois CTGAN](https://stillwell.cee.illinois.edu/data/) | 12.000 yapay olay özelliği, 6 dengeli sınıf | Yapay veri yöntemi karşılaştırması | Sentetik olarak ayrıldı; gerçek hane veya bağımsız saha sınaması sayılmaz |
| [İBB ilçe tüketimi](https://data.ibb.gov.tr/dataset/ilce-bazinda-su-tuketim-miktari) | 2000–2015 ve 2015–2022 yıllık dosyaları, 2023 aylık çalışma sayfası | F03 için ayrı Türkiye bölgesel referansı | İBB açık veri lisansı. Yıl/ay, çakışan sürümler, dış hizmet alanları ve toplamlar ayrı |
| [NASA POWER](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) | Seçilen 40,77 / 29,94 referans noktasında 1981–2025, 16.436 gün: T2M/min/max ve yağış | Kocaeli araştırması için uzun iklim bağlamı | Grid/reanaliz ürünü; hane sensörü veya tahmin anında bilinen hava öngörüsü değil |
| [Aalborg v1](https://zenodo.org/records/15180141) | Mevcut 87.360 saatlik kayıt + ayrı günlük kohortunun 2.928 kaydı | Önceki F01 sonucunu korumak, ayrı kohortu incelemek | CC BY 4.0. İşlenmiş hacim ve tüketimden türeyen etiketlerin sınırları korunur |

### Kaynakların içinde bulunan önemli sorunlar

**HSB:** Sözlük `value` için litre diyor. Kaynağın R5 kodu ve doğrulama JSON'u
aynı değerleri m³ olarak topluyor. Bizim toplamımız **3270,789 kaynak birimi**;
yayıncının bina m³ toplamıyla aynı. Bu, birim sorununun güçlü kanıtı; otomatik
1000 çarpanı uygulamak için sessiz bir gerekçe sayılmadı. Kanonik birim boş kaldı.
R5 ayrımında 16.416 kayıt ve 6 sensör konut dışı. Kalan konut kapsamı ortak
çamaşırhane/alanları da içeriyor; 28 bağımsız tam hane sayacına dönüşmüyor.
Sıfır kayıtlar ve 0,3 üstündeki değerler yayımlanmadan önce çıkarılmış.

**hihAigua:** `volume` yalnız su sayaçlarında bulunmuyor; ısıtma/soğutma devresinde
de var. Cihaz registry'siyle su ve sıcak su ayrıldığında 20 akışta **1.447.794
kayıt**, bir boş değer, bir endeks düşüşü ve **22.916 adet 10,5 dakikayı aşan
komşu zaman aralığı** bulundu. Son sayı eksik satırların kesin sayısı değildir.
Yayıncı scriptindeki sıfırla boşluk doldurma benimsenmedi. Sıcak/soğuk suyun
tesisat ilişkisi bilinmeden ikisini toplamak, toplamı iki kez sayabilir.
Epoch milisaniyeleri, saat dilimsiz metin zamanlar ve laboratuvar etiketlerinin
yerel saati ayrıca uzlaştırılmalı. Laboratuvar olayları doğal ev kaçakları değildir.

**CIWS:** Makaledeki toplanmış olay sayısıyla indirilen eğitim dosyasının satır
sayısı aynı değil; bu pakette **537** manuel eğitim satırı var. Var olmayan test
etiketlerini tamamlanmış saymıyoruz. Hane nüfusları dosyada 2/4/8/7/5; ilişkili
makaledeki tabloda farklı değerler var. Sürüm/hane eşlemesi doğrulanana kadar
bağlam bilgileri doğrulanmış kurumsal özellik olarak kullanılmayacak.
`Results/All_Events_L.csv` ve hane/mevsim dosyaları aynı sonuçların farklı
görünümleridir; birleştirip örnek sayısını artırmıyoruz. Ham pulse dosyalarında
4 saniye düzeninden sapan 9 komşu aralık var; bunlar sıfırla doldurulmadı.

**Illinois:** Veri sayfasındaki genel “etiketli” ifadesi bütün paketleri manuel
truth yapmıyor. Bağlam paketinin sütunu `Predicted_Label`. Duş paketinde hacim
değil ortalama debi ve süre var; armatür izlerinde zaman göreli. Bir hane ve
araştırmadan gelen farklı paketleri bağımsız saha kohortları gibi saymıyoruz.
Dokümantasyonda açık veri lisansı bulunmayan paketler yerel inceleme için tutuldu;
otomatik eğitim/yayımlama izni varsayılmadı.

**İBB:** 2023 açıklaması “yıllık” dese de sayfa başlıkları `202301`–`202312`;
hazırlanan kayıtlarda aylık olarak tutuldu. 2015 için **37 tam ad/dönem eşleşmesinin
37'sinde değerler farklı**. `GOP`/`G.O.PAŞA` ve `SALTANGAZİ`/`SULTANGAZİ` ayrıca
isim incelemesi gerektiriyor; otomatik düzeltilmedi. Eski dosyadaki `KOCAELİ` ve
`GEBZE-İSKİ` satırları İstanbul ilçesi veya Kocaeli'nin eksiksiz tüketimi sayılmadı;
`external_reporting_area` olarak korundu. İlçe sınır değişimleri ve eski sıfırların
anlamı da araştırılmadan tek uzun panel oluşturulmamalı.

**Meteoroloji:** NASA yanıtı UTC, sıcaklık °C ve yağış mm/gün. `-999` eksik
işareti null'a çevriliyor; bu indirmede dört değişkende de böyle bir eksik yok.
Seçilen nokta bir araştırma referansı; gerçek sayaç konumu değil. İstanbul
tüketimine veya yabancı hanelere otomatik eklenmedi. Kaynak, servis sürümü
`v2.10.2` ve erişim tarihi atıfta korunmalı.
[NASA atıf rehberi](https://power.larc.nasa.gov/docs/referencing/).
Katalog NASA için uydurma bir CC lisansı veya SPDX kodu atamıyor: açık API erişimi
ve atıf rehberi doğrulandı, veri yanıtında ayrı bir lisans tanımlayıcısı bulunmadı.

## Bizim 51 alanımızı ne ölçüde karşılıyor?

[Alan bazında JSON envanteri](../research/reference_data/public-data-field-coverage.v1.json)
planlama haritasındaki **51 alanın tamamını** tek tek ele alır. Bu bir otomatik
sağlayıcı eşleme dosyası değildir; hazır, kısmi, türetme bekleyen ve bulunmayan
alanları gösterir.

- Pulse/endeks/zaman, cihaz kimliği, bazı hane/armatür bağları ve kullanım
  olayları için araştırma örneklerimiz var. Birbirleriyle aynı ölçüm anlamına gelmiyor.
- Nüfus/yaş grubu/sulama ve cihaz varlığı sınırlı bağlam sağlar; bizim soru
  formumuzun doldurulmuş yanıtları yerine geçmez.
- Gelir, eğitim, çalışma/medeni durum, kendi katılımcılarımızın onayı ve
  abonelik eşlemeleri tamamlanmış değil. Bunlar tüketim örüntüsünden üretilmeyecek.
- Doğrulanmış gerçek kaçak, misafir/boş kalma, kurumsal bağlantı arızası truth'u,
  kalibrasyon/hata eğrisi ve geçmiş hava tahminlerinin yayım zamanı bulunmadı.
- Tarihsel `received_at` bilinmeyen kaynağa indirme tarihini eski erişim zamanı
  gibi yazmıyoruz. Kaynak geçişindeki as-of kontrolü yine gerekli.

## Depolama ve tekrar çalışma

```text
research/reference_data/
  public-source-catalog.v1.json       Sabit kaynak sürümleri, URL, hash, lisans, kullanım kapıları
  public-data-field-coverage.v1.json  51 alan için mevcut karşılıklar ve eksikler
  public-library-audit.v1.json        Paylaşılabilir kompakt sayısal denetim
var/data-library/
  <source-id>/raw/                    Değiştirilmeyen kaynak dosyaları ve .receipt.json
  istanbul-district-consumption/
    worksheet-snapshot.json          Özgün Excel'lerden salt okunur hücre çıkarımı
  prepared/<run-id>/                 Ayrı Parquet tabloları, dokümantasyon, profiles.json
  prepared/latest.json               Son başarılı hazırlamaya işaretçi
  verification.json                  Tüm 56 dosyanın çevrimdışı kontrolü
  audit.json                         Ayrıntılı güncel denetim
```

`var/` Git dışıdır. Kaynak/protokol ve denetim özetleri Git'te izlenebilir;
ham veriyi çoğaltıp repo geçmişini büyütmüyoruz. İndirme önbelleği tekrar ağdan
çekmek yerine hash doğrular. Bozulmuş/manifesti olmayan dosyanın üzerine sessizce
yazılmaz. Yeni yayıncı sürümü yeni katalog incelemesi gerektirir.

Yeni hazırlama ayrı koşum dizinine yazılır. CSV tablolarında `_source_id`,
`_source_file`, `_source_row` vardır. Kaynak sütunları string olarak korunur;
boş değer, `0`, `NaN` metni veya sınıf kodu kendiliğinden birbirine çevrilmez.
Yalnız açıklanmış meteoroloji ve bölgesel değerler ayrı tipli tablolara dönüştürülür.
Dosya adları, hane kimlikleri veya tarihler üzerinden kaynaklar arası tahmini join yoktur.

Repo kökünde PowerShell:

```powershell
.\.tools\bin\uv.exe sync --locked --extra research --extra notebooks
.\.venv\Scripts\python.exe scripts/collect_public_data.py
# Yalnız yerel dosyaları yeniden doğrulamak için:
.\.venv\Scripts\python.exe scripts/collect_public_data.py --offline

# Excel yalnız okunur; Codex bundled analysis Python'ında openpyxl hazırdır.
$analysisPython = 'C:\Users\CODEIDENT1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $analysisPython scripts/extract_regional_workbooks.py
.\.venv\Scripts\python.exe scripts/prepare_public_data.py
.\.venv\Scripts\python.exe scripts/audit_public_data.py
```

Başka bilgisayarda Excel çıkarımı için `openpyxl` içeren ayrı bir analiz Python'ı
kullanılabilir; uygulama/ingestion ortamına bu bağımlılık eklenmedi. CSV hazırlama
akışında Excel dosyası değiştirilmez ve indirilen kod çalıştırılmaz.

## Eğitim için sonraki sıra

1. **hihAigua:** birim, sıcak/soğuk su kapsamı ve saat kararlarını kanıtlarıyla
   kaydet. [Endeks adaptörü ve korumalı yayın yolu hazırlandı](hihaigua-reference.md);
   gerçek verinin kanonik yayını birim kanıtı bekliyor.
   Sonra ayrı F01/F02 protokolleri ve yeni dış test dönemi belirle.
2. **CIWS:** pulse ölçeği ve hane metadata uyuşmazlığını çöz. Manuel eğitim
   etiketlerini tahmin çıktısından ayırarak tüketim/olay referans deneyi kur.
3. **HSB ve Illinois:** birim/lisans/kapsam kapılarını çözerek armatür şekli ve
   dağılımlarından etiketli senaryo tasarımına dayanak çıkar. Olayları birleştirerek
   bütün gün üretirsek sonuç açıkça **sentetik** olacak; ölçülmüş gün denmeyecek.
4. **Bölgesel hat:** İBB sürüm/isim/kapsam kararlarını ayrı incele. Kuraklık için
   temsil edici Kocaeli tüketimi, nüfus ve uygun uzamsal iklim eşlemesi gerekiyor.

Mevcut ortak hedef korunur: **yerel dosya veya API → kaynak adaptörü → kanonik
veri katmanı → dondurulmuş veri seti → aynı özellik/model kodu**. Yeni şema için
adaptör gerekir; modelin içine yayıncı dosya sütunları taşınmaz.

### Kütüphanenin ilk doğrulaması

39 hedefli test geçti: kaynak dosyası değişikliği, hash uyuşmazlığı, indirme boyutu,
dizin sınırı, boş/sıfır ayrımı, satır kökeni, etiket türü, eksik/endeks düşüşü,
bölgesel dönem/kapsam ayrımı ve mevcut günlük tahmin regresyonları sınandı.
Değişen Python dosyalarında Ruff lint/format ve `git diff --check` geçti.
56 ham dosya, 68 CSV Parquet'i ve iki türetilmiş Parquet hash/satır sayılarıyla
doğrulandı. Mevcut günlük deneyin 36 çıktısı ayrıca doğrulandı. Kurum API'si,
gerçek kurum verisi ve yeni kaynaklar üzerinde model eğitimi bu kontrollerin parçası değil.
Sonraki hihAigua adaptörünün test ve gerçek dosya kanıtı
[ayrı kaynak raporunda](hihaigua-reference.md) kayıtlıdır.

## Erişilemeyen veya kapsam dışı bırakılan adaylar

- [AMPds2](https://makonin.com/ampds/): resmi veri sahibi ve makalesi bulundu.
  [Harvard kaydı](https://doi.org/10.7910/DVN/FIE0S4) / indirme API'si bu oturumda
  erişim kontrolü/HTTP 403 verdi. Su dosyaları ve veri lisansı doğrulanmış sayılmadı.
  Üçüncü taraf kopyayla kaynak zinciri değiştirilmedi.
- Utah'ın 31 hanelik geniş çalışması bulundu; seçilen beş hane bunun alt kümesi.
  Bağımsız ek 31 hane indirilmiş gibi sayılmadı.
- [LeakDB](https://github.com/KIOS-Research/LeakDB) ve BattLeDIM, şebeke basınç/debi
  senaryoları için ayrı adaylar. Şebeke kaçağı hane içi sayaç kaçak doğrusu değil.
  Bu koleksiyonda büyük şebeke senaryo arşivleri indirilmedi.
- Kocaeli için eksiksiz, indirilebilir kurumsal tüketim/anket/kalibrasyon paketi
  doğrulanmadı. Eski İBB tablosundaki iki dış hizmet satırı bu boşluğu kapatmıyor.
