# hihAigua v2: kaynak adaptörü ve yayın sınırları

4 Kasım 2026. [Zenodo 21704780](https://zenodo.org/records/21704780) paketinin
yerel dosyalarıyla çalıştırıldı. **Kaynağa özel hazırlama ve mevcut veri katmanına
yayın yolu kodlandı. Gerçek paketin kanonik yayını hacim birimi doğrulanana kadar
kapalıdır.** Bu durum kullanıcı onayı isteyen bir adım değil; eksik kaynak kanıtıdır.

## Gerçek dosyada yapılan işlem

10 hanelik saha kurulumlarının **10.132.219 telemetri satırı** tarandı. `devs.csv`
ile cihaz kimliği/kurulum/asset bağı doğrulandı; yalnız seçilen su cihazlarının
`volume` metrikleri alındı:

| Bileşen | Sayaç akışı | Kayıt | 10,5 dakikadan uzun komşu aralık | Boş değer | Sayısal endeks düşüşü |
|---|---:|---:|---:|---:|---:|
| `water` | 10 | 723.904 | 11.449 | 0 | 0 |
| `hotWater` | 10 | 723.890 | 11.467 | 1 | 1 |

Toplam **1.447.794 kayıt** iki ayrı Parquet'te hazırlandı. Sayılar önceki açık veri
kütüphanesi denetimindeki 20 akışın kimlik/satır sayılarıyla birebir uyuşuyor.
20 sayaç 20 hane sayılmıyor. Aynı fiziksel hane kodu kaynak içindeki karşılığını
koruyor. Laboratuvar, ortam sensörü, elektrik ve ısıtma/soğutma sayaçları bu
ölçüm ailesine katılmadı. Isıtma sayacındaki `volume`, hane su tüketimi sayılmıyor.
Registry'nin geçerlilik aralığı bu referanstaki gözlem penceresidir; gerçek
donanımın kurulum/söküm tarihi olduğu iddia edilmez.

Soğuk/sıcak suyun tesisattaki ilişkisi doğrulanmadığı için bunları toplayan bir
"toplam hane tüketimi" üretilmedi. Kaynak değişse de bu ölçüm kapsamı açık kalmalı.

## Giriş, çıkış ve kodun yerleşimi

```text
Özgün ZIP + devs.csv + ts.csv
  → kaynak kimliği ve SHA-256 kontrolü
  → cihaz ailesi seçimi / epoch UTC / hassas endeks / kalite / satır kökeni
  → water/counters.parquet + hotWater/counters.parquet
  → birim kaynak kanıtıyla doğrulanırsa bileşen başına:
      counter-readings.csv + registry.json + sources.toml
  → mevcut mapped_csv_v1 → ortak kabul / worker / yayın / dondurma
```

- [Kaynak politikası](../research/reference_data/hihaigua-v2.json): sabit ZIP,
  kaynak kurulumları, zaman temeli, birim kararları, boşluk eşiği ve model sınırları.
- `packages/core/src/urban_water/sources/hihaigua_reference.py`: kaynak hazırlama,
  bütünlük kontrolü ve tek bileşen için korumalı yayın orkestrasyonu.
- `scripts/run_hihaigua.py`: yerel çalıştırma. İlk çalıştırma DB'ye bağlanmaz.
- [İnceleme notebook'u](../notebooks/04_hihaigua_reference.ipynb): manifest,
  kayıt/kalite sayıları ve ham/sözleşme değerlerini gösterir.
- [Kaydedilmiş denetim](../research/reference_data/hihaigua-adapter-result.v1.json):
  gerçek koşumun SHA-256, kayıt sayıları, birim kapıları ve doğrulama durumu.

Yeni bir reader, worker, veritabanı şeması veya model bağımlılığı eklenmedi.
Yayın aşaması mevcut `mapped_csv_v1` arabirimini kullanıyor. Aynı biçimde CSV
sunabilen bir HTTP kaynağına geçiş reader ayarıyla yapılabilir. Farklı API
protokolü/sayfalama/JSON şeması gelirse kaynak adaptörü gerekir; modelin içine
sağlayıcı sütunları taşınmaz.

## Zaman ve sayısal anlam

`ts` integer epoch milisaniyesi UTC olarak yorumlandı. Bu yorum, kaynağın
`processing_script.py` dosyasındaki milisaniye çözümlemesiyle uyumlu. `ts_dt`
özgün metni ayrıca korunup epoch karşılığıyla karşılaştırıldı. Gerçek seçilen
kayıtlarda bu temsiller arasında uyuşmazlık veya geriye/aynı ana dönen zaman yok.
Bu, iki zaman alanının tutarlılığıdır; cihaz saatinin fiziksel doğruluğunu kanıtlamaz.
Kaynak açıklamasındaki GMT+1 ifadesiyle metne sessizce saat farkı eklenmedi.

`received_at` gerçek **2026 arşiv indirme zamanı**. 2025'te cihazdan geliş anı gibi
yazılmadı. Geçmişte hangi tahmin anında hangi kaydın erişilebilir olduğunu bu
arşiv tek başına kanıtlamıyor. İstanbul tez takvimi değişmedi; bu ham referans
katmanı UTC kullanıyor. Yerel tahmin takvimi ayrı protokol kararıdır.

Kaynakta sıcak su değerlerinin 98.970 satırında `32.001999999999995` gibi uzun
ondalık gösterimler var. Bunlar eksik veya bozuk ölçüm sayılmadı:

- `value_source`: özgün metin.
- `counter_value`: yüksek hassasiyetli ondalık kaynak değeri; 40 kesir hanesine kadar.
- `contract_value`: ortak sözleşmenin dokuz kesir hanesine HALF_EVEN ile uyarlanan
  ayrı değer. Kaynak değerinin üzerine yazılmaz.
- `contract_adjustment_source`: uygulanan fark. Teorik üst sınır 0,0000000005
  kaynak birimi; gerçek dosyada en büyük fark **0,00000000000002** kaynak birimi.

Birim henüz doğrulanmadığından bu farklar litre diye raporlanmadı. Sözleşmeye
uyarlanmış değer ancak kaynak birimi kanıtlandığında yayın dosyasına geçer.
Özgün değer, kaynak üyesi/satırı ve kalite alanları o CSV'de de korunur.

## Eksik, endeks düşüşü ve kapsama

Boş değerin sayısal karşılığı null'dır; sıfır ölçüm ayrı kalır. Kaynak inceleme
tablosundaki `counter_delta_source`, boş/uygunsuz öncül üzerinden fark üretmez.
Sayısal endeks düşüşünde negatif fark korunur ve bayraklanır. Gün başında endeks
sıfırlanmaz. Zaman boşlukları örnek uydurarak doldurulmaz. Düşüş veya boşluk
otomatik kaçak/cihaz arızası etiketi değildir.

Yayınlanan kanonik kayıt türü **index** olur. Ortak özetleme mevcut geçerli
endeksler arasında fark alır; karantinaya alınan bir ara okuma üzerinden
kümülatif fark oluşabilir. Bu, tüm ara örneklerin bulunduğu anlamına gelmez.
Kaynak tablosundaki boşluk/bariyer bayrakları ve ham CSV kökeni ayrıca korunur.
Kanonik özetlerin `coverage_status=unknown` değeri değişmez; bunlar eksiksiz
zaman serisi diye sunulmaz.

Önemli ek sınır: Yedi dakikalık sayaç okumaları çoğunlukla tam saat/gün sınırına
denk gelmez. Ortak çekirdek sınırı aşan aralığı varsayımsal oranla bölmez;
hacmi `unallocated_liters` içinde raporlar. **24 saat satırının bulunması günlük
hacmin eksiksiz olduğu kanıtı değildir.** F01/F02 etiketi için sınır paylaştırma,
kapsama ve hedef tanımı ayrıca belirlenmeli. Bu nedenle `model_ready=false`.

5 Kasım ek çalışması: [kayıtlı endeksten günlük tek değer ve sınır çıkarımı](hihaigua-counter-days.md)
gerçek veriyle çalıştırıldı. Bu ek yöntem boşlukları doldurmuyor; birim ve model
eğitim kapılarını değiştirmiyor.

## Birim kapısı ve çalıştırma

Paketin cihaz kayıtları ve erişilebilen yayın metadata'sı sayısal `volume`
alanının birimini açıkça belirtmiyor. İlgili
[makalenin](https://doi.org/10.1038/s41597-026-08485-5) tam PDF'sine bu oturumda
erişilemedi. Donanımın adı veya sayısal değerin makul görünmesi, bütün veri
akışlarının ölçeğini doğrulamak için yeterli kabul edilmedi.

Politikada iki bileşenin `unit_review.status=pending`, `unit=null` durumu korunur.
Bu durumda yalnız kaynak Parquet'leri hazırlanır; `sources.toml` ve litreli
kanonik dosya üretilmez. Yayın fonksiyonu DB'ye erişmeden reddeder. Hazırlama
manifestindeki bir hazır bayrağını değiştirmek kaynak politikasını geçersiz kılamaz.

Repo kökünde:

```powershell
.\.venv\Scripts\python.exe scripts/run_hihaigua.py
.\.venv\Scripts\python.exe scripts/run_hihaigua.py --require-canonical
```

İlk komut mevcut dosyaları doğrular/hazırlar. İkincisinin bu kaynak durumunda
**çıkış kodu 2** vermesi beklenir: inceleme hazır, kanonik yayın kapalı.
Yeni kaynak/uyarlama sürümü ayrı koşum dizinine yazılır. Tamamlanmamış/bozulmuş
dizinin üzerine sessizce yazılmaz; gerekirse yeni `--output` dizini kullanılır.

Birim kanıtı bulunursa `verified`, `L` veya `m3`, kanıt URL'si ve ilgili açıklama
kaydedilir. Bu yalnız bir ayar seçimi değil, kaynak anlamının doğrulanmasıdır.
Tüm bileşen sayaçları için geçerli olduğu doğrulanmalıdır. Sonra aynı hazırlama
komutu kanonik kaynak noktalarını üretir.

Kanonik yayın için ayrıca kurulmuş ve migration'ları uygulanmış **ayrı araştırma
DB'si** ve açık `--env-file` gerekir; bu runner DB oluşturmaz, migrate etmez veya
sıfırlamaz. Ortak `uw --env-file PATH migrate` komutu ayrı kurulum adımıdır.
Kaynak birimi beklerken bu aşama
gerçek veri üzerinde çalıştırılmadı. Birim doğrulanınca:

```powershell
.\.venv\Scripts\python.exe scripts/run_hihaigua.py --import-component water --env-file PATH
```

`water` ortamı `public_research`, UTC, çalışma `hihaigua-21704780-water`, kohort
`field-water-v2` olmalı. `hotWater` için adlar ilgili bileşene göre değişir ve
**ayrı DB/storage** kullanılmalı. Runner beklenen çalışma/kohortu ve DB'de başka
çalışma/kaynak ailesi bulunmadığını denetler. İki bileşen bu korumalı yolla aynı
DB'ye yayınlanamaz. Alt seviye genel CLI'yi elle kullanmak bu özel kapsam
kontrolünü sağlamaz.

Ortam değişkenleri `.env` dosyasından önce gelir. Yanlış aktif ortam beklenen
çalışma kapsamıyla uyuşmazsa işlem reddedilir. Gizli DB bilgileri rapora yazılmaz.
Ham kütüphane, hazırlama manifestleri ve DB storage birlikte yedeklenmelidir;
yalnız DB yedeği kaynak ZIP'inin de yedeklendiği anlamına gelmez.

## Doğrulananlar ve açık kalanlar

**265 test geçti, atlanan test yok.** Kaynağa özel 20 test; boş/sıfır farkını,
gece yarısı devamını, endeks düşüşünü, zaman uyuşmazlığını, hassasiyet sınırını,
kimlik çakışmasını, dosya bozulmasını, birim kapısını ve bileşen ayrımını sınar.
Notebook'un üç kod hücresi de hatasız çalıştırıldı.

Kontrollü **test fixture'ında tanımı verilmiş m³** kullanılarak gerçek PostgreSQL
üzerinde registry → collect → worker → karantina → özet → freeze/verify zinciri
geçti. Yeniden aktarım tüketimi artırmadı. Bu, public paketin biriminin
doğrulandığı veya public verinin DB'ye aktarıldığı kanıtı değildir.

Kaynak sahibinden veya ayrıntılı teknik belgeden netleştirilecek sorular:

1. Her iki `volume` alanı ve bütün yayımlanan sayaçlar için sayısal birim nedir?
   Ham M-Bus değeriyle yayınlanan `value` arasında ölçek dönüşümü var mı?
2. `water` sayacı sıcak suya giden kolu da ölçüyor mu, yalnız ayrı soğuk suyu mu?
3. Yerel saha/etiket takvimi hangi IANA saat diliminde ve yaz saati nasıl uygulanıyor?
4. Tarihsel cihaz okuma/iletim zamanı veya bağımsız arıza/kaçak doğrulaması var mı?

Bu sorular bir dosyada eksik alanı tahminle doldurmak için kullanılmıyor. Gerçek
paketin yayın ve model hazırlığı bu kanıtlar ve ayrı hedef/kapsama kararıyla ilerler.
