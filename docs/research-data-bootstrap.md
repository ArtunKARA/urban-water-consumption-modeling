# Sentetik veri, açık araştırma verisi ve sağlayıcı API'si

Tarih: 29 Ekim 2026. Durum: **ilk sayaç adaptörleri uygulandı; veri setine özel
eşleme ve eğitimler sonraki iş**. [Çalışan kaynak yolu](source-adapters-usage.md).

Amaç: Önce sentetik veriyle, sonra erişilebilir bir araştırma veri setiyle,
sonunda kurum API'siyle aynı araştırma kodunu çalıştırmak. Kaynak değişimini
bağlantı ve alan eşlemelerinde tutmak; veri anlamındaki farkları görünür kılmak.

## Kaynak değişirken modelin gördüğü

Üç giriş yolu aynı ortak yayın katmanında birleşir:

| Giriş | Kaynağa özgü bölüm | Ortak katmana verdiği |
|---|---|---|
| Sentetik üretici | Senaryo, seed, üretici sürümü | Ölçümler ve ayrı bağlam kayıtları; gizli truth ayrı değerlendirme dosyası |
| Açık araştırma veri seti | Sabitlenmiş dosya sürümü ve veri sözlüğüne uygun eşleme | Birimi, zaman aralığı ve ölçüm kapsamı belli gözlemler |
| Kurum API'si | Kimlik doğrulama, sayfalama, tekrar deneme, sağlayıcı eşlemesi | Aynı ortak kayıt aileleri; kaynak ve revizyon kökeni korunur |

Akış: **kaynak → özgün nesne → dönüştürme → ortak doğrulama/yayın →
dondurulmuş veri seti → özellikler → model → çıktı**.
[Adaptör tasarımı](source-adapters.md) katmanların görevlerini tanımlar.

Model doğrudan API çağırmaz, CSV açmaz, sağlayıcı sütun adını bilmez.
Deney, sabitlenmiş veri seti kimliği ve özellik sözleşmesiyle çalışır.
Canlı tahmin ileride aynı özellik üretiminin belirli bir `as_of` anındaki
görünümünü kullanır; eğitim içinde değişen canlı veriye sorgu yapılmaz.

Hazır adaptör ve uyumlu kayıt ailesinde kaynak değişimi ayarla yapılır. Yeni
protokol veya şema için yeni reader/normalizer gerekir. Yeni bir araştırma
değişkeni gerçekten gerekiyorsa özellik sözleşmesi ve deney de değişir.
Dolayısıyla hedef **model kodunda en az değişikliktir**; her farklı veri için
hiç kod yazmama sözü değildir.

## Kodun yerleşimi

Aşağıdaki yerleşimde `sources/` ve örnek kaynak ayarları uygulanmıştır;
araştırma referansları ve geniş simülatör yerleşimi sonraki adımdır:

```text
packages/contracts/                         Sürümlü ortak kayıt türleri
packages/core/src/urban_water/sources/
  config.py                                 Doğrulanmış veri noktaları ve eşleme ayarları
  readers.py                                SourceReader; yerel dosya ve tek HTTP nesnesi
  normalizers.py                            SourceNormalizer; JSONL ve eşlemeli CSV
  registration.py                           Açık hane/sayaç/kurulum kaydı
packages/simulator/                         Senaryo üretimi; model eğitimi içermez
configs/sources/                            Gizli bilgi içermeyen kaynak seçimleri
research/reference_data/                    Kaynak künyeleri, eşlemeler, kalibrasyon profilleri
research/experiments/                        Mevcut deney protokolleri ve koşum tanımları
var/                                        Git dışı özgün dosyalar, snapshot ve sonuçlar
```

İlk aşamada yeni servis, mesaj aracısı veya her kaynağa ayrı Python paketi
gerekmiyor. Mevcut CLI, worker ve PostgreSQL kabul/yayın yolu korunur.
API parolası/token'ı yapılandırma dosyasına veya deney manifestine yazılmaz.

## İlk açık veri adayı ve iki tamamlayıcı kaynak

Kaynaklar 28 Ekim 2026 tarihinde birincil yayıncı kayıtlarından incelendi.
Bir veri setinin bütün araştırma alanlarımızı sağlaması beklenmez.

| Kaynak | Doğrulanan kapsam | Bu proje için önerilen kullanım | Sınırı |
|---|---|---|---|
| [Aalborg Residential Household Dataset, v1](https://zenodo.org/records/15180141) ve [teknik raporu](https://doi.org/10.54337/aau780546283) | Danimarka; 10 konutta yaklaşık bir yıllık saatlik su ve tüketimden elle etiketlenen günlük doluluk; ayrıca 8 binada günlükle kaydedilen saatlik doluluk ve su | **İlk aday:** tüketim geçmişi, gün içi örüntüler, tüketim temelli profil ve ilk tahmin deneyleri; günlükle ölçülen bağlamı ayrı deneyde inceleme | Gelir, bizim anketimiz ve doğrulanmış kaçak etiketleri yerine geçmez. Tüketimden türetilen doluluk bağımsız açıklayıcı gerçek değildir. |
| [HSB Living Lab, v1.1.0](https://zenodo.org/records/22076411) | İsveç; 214 sayaç, 28 daire, 1 L çözünürlük, 10 dakika raporlama; armatür düzeyinde veriler ve veri sözlüğü | İkinci aşamada kullanım türleri ve sentetik kullanım olaylarının dayanaklarını inceleme | 214 sayaç 214 hane değildir. Konut/ortak alan ve alt sayaç sınırları çözülmeden toplanmaz; tam hane tüketimi varsayılmaz. |
| [AMPds2 veri sahibi sayfası](https://makonin.com/ampds/) ve [Scientific Data makalesi](https://doi.org/10.1038/sdata.2016.37) | Kanada; tek ev, iki yıl, bir dakika aralıklarla su/elektrik/gaz; temizleme ve eksik değer doldurma açıklanıyor | Uzun seri okuma, zaman ve yeniden örnekleme için tamamlayıcı karşılaştırma | Tek hane çok haneli profil başarısını kanıtlamaz. Temizlenmiş veri gerçek aktarım arızalarının sıklığını temsil etmez. |

Aalborg ve HSB sabit sürüm kayıtlarının Zenodo API metadata'sında erişim `open`,
lisans `cc-by-4.0` olarak doğrulandı. Kaynak atıfları ve yapılan dönüşümler
üretilen veri seti manifestinde saklanacak. AMPds2'nin yayıncı sayfasında indirme
bağlantısı var; bu incelemede Dataverse web sayfası robot kontrolüne takıldı.
AMPds2 dosyaları ve veri lisansı ayrıca doğrulanmadan içeri alınmış sayılmaz.
HSB'nin eski v1 kaydı yerine v1.1.0 sabitlenmiştir; `latest` çalışma girdisi olmaz.

### İlk adayın dosyalarında yapılan küçük kontrol

Aalborg `final_data.zip` (426.228 bayt) bellekte açıldı; araştırma DB'sine
yüklenmedi. İki CSV'nin başlıkları, kayıt sayıları ve bina başına zaman aralıkları
kontrol edildi:

- `daily_data.csv`: 87.360 satır, 10 bina; bina başına 8.736 benzersiz zaman
  damgası. `water_use_l`, `date_time`, `date`, `occ_01`–`occ_05` alanları var.
  Dosya adındaki `daily`, tüketim satırının günlük olduğu anlamına gelmiyor.
- `hourly_data.csv`: toplam 2.928 satır, 8 bina. Bina başına 168–744 kayıt;
  toplam 122 bina-gün. Sekiz binanın her birinde 122 gün olduğu varsayılmayacak.
  `occupied`, `washing`, `dishwasher` ve su/elektrik alanları var.
- Bu sayım eksiksizlik veya ölçüm doğruluğu onayı değildir. Zaman damgasının
  dönem başını mı sonunu mu gösterdiği, yerel gün tanımı, doldurma işlemleri,
  kod kitabı ve sıfırların anlamı adaptör kabulünden önce çözülecek.

Teknik raporun üniversite sayfası incelendi; PDF bağlantısı bu oturumda 403
döndürdü. Tam yöntem doğrulaması bitmedi. Özellikle tüketimden türetilmiş
doluluk etiketlerini gelecekteki tüketimin tahmininde kullanmak veri sızıntısı
oluşturabilir; ilk tüketim-geçmişi deneyi bu alanları özellik olarak almayacak.

## Bir sonraki somut teslimat

| Sıra | İş | Tamamlandı diyebilmek için gereken |
|---|---|---|
| 1 | Mevcut JSONL yolunu arabirim arkasına almak; kayıtlı adaptör seçimini eklemek | Aynı dosya için hacim, kalite, revision, tekrar teslimat ve yeniden başlama sonuçları korunmalı; bilinmeyen sürüm açıkça reddedilmeli |
| 2 | Aalborg sözlüğünü doğrulamak ve salt dosya adaptörünü eklemek | Lisans/sürüm/hash, birim, dönem sınırı, bina kapsamı, eksik/doldurulmuş veri raporu ve eşleme kaydı bulunmalı |
| 3 | Küçük bir dondurulmuş tüketim veri seti ve ilk karşılaştırmayı üretmek | Zaman sıralı train/validation/test; mevsimsel naif ve geçmişe dayalı ilk model; veri yeterliliği ve dışlanan örnekler raporlanmalı |
| 4 | Eğitim bölümünden sentetik davranış profili çıkarmak | Saat/gün örüntüsü, sıfır kullanım süreleri, değişkenlik ve bağımlılıklar için gerçek/sentetik karşılaştırması; parametrelerin kökeni ve belirsizliği görünmeli |
| 5 | Senaryoları eklemek ve aynı akışa vermek | Kaçak, normal davranış değişimi ve cihaz/aktarım sorunu ayrı truth/rapor; truth özelliklere girmemeli |
| 6 | Kontrollü yerel HTTP kaynağıyla kaynak değişimini sınamak | Dosya ve HTTP aynı ortak olayları/dataset sonucunu üretmeli; sayfalama, timeout, duplicate, revision ve cursor sonrası kapanış sınanmalı |
| 7 | Gerçek sağlayıcı örneğiyle API adaptörünü doğrulamak | Kimlik, saat, birim, yeniden gönderim ve düzeltme semantiği gerçek anonim örnekle kanıtlanmalı; kurum geçiş koşulları tamamlanmalı |

İlk küçük karşılaştırma, ilk **kapsamlı** sentetik teslimatın kabul edilen üç
araştırma hattını daraltmaz. P01'in iki yaklaşımı, F01'in 18:00/23:00 ayrı
deneyleri, F02 saatlik tahmin ve A01/V01'in ayrı senaryoları
[mevcut eğitim planında](contracts/model-training-plan.md) kalır. Her kaynak
yalnız gerçekten sağladığı veri ve etiketlerle ilgili deneylere girebilir.

## Sentetik üreticiyi araştırmaya uygun geliştirme

Mevcut üretici küçük bir doğruluk fixture'ıdır. İleri üretimde üç katman ayrılır:

1. **Kullanım:** hane rutini, gün/takvim etkisi, kullanım olayları ve fiziksel hacim.
2. **Ölçüm:** kurulum, sayaç temsili, çözünürlük ve varsa bağımsız dayanaklı hata.
3. **Aktarım:** gecikme, eksik teslimat, tekrar, sıra değişimi ve düzeltmeler.

Saatlik toplam, saniyelik kullanım olaylarının gerçek biçimini veya 100 mL
pulse zamanlarını belirlemez. Saatlik referansla saatlik sentetik profil
kalibre edilebilir; daha ince olay üretimi ayrıca varsayım veya uygun ince
çözünürlüklü kaynak gerektirir. Varsayılan olaylar ölçülmüş gerçek diye sunulmaz.
Sayaç doğruluk eğrisi de yalnız tüketim serisinden çıkarılamaz.

Önce gerçek verinin bölmeleri sabitlenir; üretici parametreleri yalnız eğitim
bölümünden öğrenilir. Model/üretici seçimleri validation'da yapılır; test bölümü
son değerlendirmeye saklanır. Sentetik örnekler hangi gerçek hane/dönemden
türetildiyse o bölmenin sınırını korur. Aynı serinin kopyaları train ve test'e
dağıtılmaz. Aynı seed tekrar üretilebilirlik sağlar; gerçekçilik kanıtı sağlamaz.

Gerçek açık veri, tamamen sentetik veri ve gerçek tüketim üzerine kurgulanmış
bağlam/arıza eklenen melez veri ayrı etiketlenir. Yabancı bir veri setinin hanesi
İSU hanesiyle tahminen eşleştirilmez. Eksik anket alanları doldurulmuş gerçek
cevap gibi sunulmaz. Gelir etkisi için veri yoksa bu ilişki öğrenilmiş sayılmaz.

Eski veride gerçek sağlayıcı erişim zamanları yoksa 18:00/23:00 deneyi açıkça
tanımlanan bir erişim gecikmesi varsayımıyla yapılır; tarihsel çevrimiçi servis
performansı diye sunulmaz. Yabancı kaynağın yerel takvimi korunur; İstanbul
saatleriyle yapılacak ayrı simülasyon açıkça etiketlenir. Gözlenen gelecek hava,
tahmin anında bilinen hava tahmini yerine kullanılamaz.

## Mevcut uygulama sınırı

29 Ekim uygulaması yerel/HTTP, JSONL/CSV sayaç yolunu ekledi. Kaynak eşlemesi ve
sürümü kabulde sabitlenir. Ayrı `UW_ENV=public_research` ortamı kaynak tarafsız
`urban-water.measurement.v1` kullanır. Genel araştırma paket şeması bütün kayıt
aileleriyle henüz CLI girdisi değildir. Aalborg'a özel dönem/zaman eşlemesi,
gerçek açık verinin içeri alınması, sentetik kalibrasyon ve model eğitimleri
henüz yapılmadı. Kontrollü HTTP kanıtı kurum API'si doğrulaması değildir.
