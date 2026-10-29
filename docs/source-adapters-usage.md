# Kaynak noktalarını çalıştırma

29 Ekim 2026. Çalışan ilk kapsam: **sayaç ölçümleri; yerel dosya veya tek HTTP
yanıtından JSONL/CSV → ortak ingestion/kalite/özet/dataset yolu**.

## Hazır yerel örnek

Repo kökünde, mevcut sentetik geliştirme ortamında:

```powershell
.\.venv\Scripts\uw.exe migrate
.\.venv\Scripts\uw.exe register examples/sources/registry.json
.\.venv\Scripts\uw.exe collect meter_readings --config configs/sources/local-demo.toml
.\.venv\Scripts\uw-worker.exe --once
.\.venv\Scripts\uw.exe summarize --grain day
.\.venv\Scripts\uw.exe freeze --grain day
```

Örnek tamamen kurgusaldır: üç saat için **12,5 + 0 + 7,25 = 19,75 litre**.
Önceki verilerin bulunduğu DB'de özet bunları da içerebilir; örnek hane `DEMO-H01`.
Komutları yeniden çalıştırmak bu ölçümleri çoğaltmaz. Ayrı DB'de, gerçek CLI ve
yerel HTTP sunucusuyla tekrarlanabilir kanıt:

```powershell
.\.venv\Scripts\python.exe scripts/prove_source_adapters.py
```

Script yalnız işaretli yerel PostgreSQL kümesinde kendi oluşturduğu geçici
`*_test` DB'yi kullanıp kaldırır. Mevcut çalışma DB'sini sıfırlamaz. Kanıt ve
dosyalar Git dışındaki `var/proofs/source-adapters/` altında kalır.

## Veri noktası hangi ayarlara sahip?

[Yerel örnekte](../configs/sources/local-demo.toml) `sources.meter_readings` bir
veri noktasıdır. Aynı dosyada birden fazla isimle bağımsız sayaç kaynağı
tanımlanabilir. Şu an yalnız `family="meter_measurements"` desteklenir.

| Ayar | Anlamı |
|---|---|
| `namespace` | Sağlayıcı/veri setinin kalıcı olay kimlik alanı. Dosya ve API aynı olayları taşıyorsa aynı kalır. |
| `reader` | `local_file_v1` veya `http_file_v1` |
| `normalizer` | `measurement_jsonl_v1` veya `mapped_csv_v1` |
| `path` / `path_env` | Yerel dosya konumu; göreli yol TOML dosyasına göre çözülür. İkisinden yalnız biri kullanılır. |
| `url_env` / `token_env` | HTTP URL'sini ve isteğe bağlı Bearer token'ını içeren ortam değişkenlerinin adları |
| `fields` | Ortak alan → kaynak sütunu eşlemesi |
| `constants` | Kaynak sözlüğüne dayanarak açıkça verilen sabitler; örneğin `unit="L"` |
| `data_kind` | `synthetic` veya `public_research`; çalışma ortamıyla aynı olmalı |
| `citation` / `license` | İsteğe bağlı veri künyesi/lisansı; kaynak ve dataset manifestine aktarılır |

Yapılandırmada keyfi Python import'u yoktur; kayıtlı sürümler seçilir.
CSV'de eksik alanlar otomatik uydurulmaz. Geçerli `received_at` bilinmiyorsa
dosyanın indirme zamanı sağlayıcının geçmiş teslimat zamanı gibi yazılmaz.
Önce veri sözlüğü ve erişim zamanı varsayımları netleştirilir.

## Aynı noktanın HTTP'ye geçmesi

[HTTP örneği](../configs/sources/http-demo.toml), aynı namespace ve eşlemeyi
koruyup yalnız okuyucu/konum ayarını değiştirir:

```powershell
$env:UW_METER_URL = 'https://ornek-saglayici.example/export.csv'
# Gerekiyorsa TOML'ye token_env = "UW_METER_TOKEN" ekleyin; token ortamda tutulur.
.\.venv\Scripts\uw.exe collect meter_readings --config configs/sources/http-demo.toml
.\.venv\Scripts\uw-worker.exe --once
```

URL örnektir. HTTP reader tek, tamamlanmış, sıkıştırılmamış CSV/JSONL yanıtını
alır. HTTPS kullanır; düz HTTP yalnız localhost testleri içindir. Redirect takip
etmez. Boyut sınırı ve bağlantı/okuma timeout'u vardır. HTML sayfası, ZIP dosyası,
JSON `items` zarfı, OAuth yenileme, sayfalama, SSE/WebSocket ve kalıcı API cursor'ı
bu adaptör değildir. Bunlar gerektiğinde yeni reader/normalizer sürümleriyle
eklenir; yarım sayfa toplanıp tüm veri alınmış sayılmaz. Toplama tekrar denemesi
şu an komutun yeniden çalıştırılmasıdır; kabul edilmiş işler kalıcı worker'dadır.

## İnternetten alınan açık araştırma verisi

Kurgusal fixture ile kamuya açık gerçek araştırma verisi ayrı DB ve depolama
köklerinde tutulur. Ayrı provision edilmiş araştırma ortamında:

```text
UW_ENV=public_research
UW_DATABASE_URL=<ayrı araştırma DB bağlantısı>
UW_STORAGE_ROOT=<ayrı araştırma depolama dizini>
UW_STUDY_CODE=<araştırma kodu>
UW_COHORT_CODE=<örneklem kodu>
UW_TIMEZONE=<kaynağın yerel zaman dilimi>
```

Kaynak TOML'sinde de `data_kind="public_research"` seçilir. JSONL sözleşmesi
`urban-water.measurement.v1` olur; alanların anlamı
[veri sözlüğündeki](data-dictionary.md) ölçüm türleriyle aynıdır. CSV dönüştürücüsü
bu sözleşmeye doğrular. Sentetik v1 JSONL yolu geriye uyumlu kalır.

Önce `uw migrate`, sonra gerçek veri sözlüğüne göre hazırlanmış, yalnız kodlanmış
hane/sayaç/kurulum ilişkilerini içeren `uw register <registry.json>` çalıştırılır.
Ölçümler tanımsız stream için uydurma hane oluşturmaz; kaliteye ayrılır.
Registry import'u mevcut kimliği başka haneye sessizce taşımaz veya geçmiş
ayarını güncellemez. Sayaç/kurulum değişiklikleri ayrı tarihli registry işlemi
gerektirir; import dosyası genel amaçlı registry düzenleyicisi değildir.

Bu ortam ayrımı açık araştırma verisi içindir. Kurumun özel/kişisel verisini
işleme yetkilendirmesi veya hazır İSU/Manas adaptörü anlamına gelmez. Desteklenen
runtime ortamlarına `production` eklenmedi.

## Tekillik ve yeniden başlatma

- Kaynak kabulü: özgün hash + sözleşme + parser + namespace + eşleme hash'i.
  Okuma teknolojisi bu anahtarda yoktur; aynı nesneyi HTTP'den tekrar almak
  yeni tüketim oluşturmaz.
- Olay kimliği: namespace + kaynak event ID'si + stream + revision. Aynı olay
  farklı dosyada gelirse provenance korunur, hacim çoğalmaz. `legacy` namespace
  eski sentetik CLI'nin kimliklerini korur; yeni sağlayıcılarda kullanılmaz.
- Aynı namespace altında aynı olay/revision'ın farklı içeriği çatışmadır.
  Düzeltme açık revision ister. Eşleme değişikliği yeni kabul oluşturur;
  yayınlanmış olayı sessizce değiştiremez.
- Farklı namespace'ler farklı olay kimlikleridir. Aynı fiziksel ölçümün dosya
  ve API kopyalarına farklı namespace vermek fiziksel eşleştirme sağlamaz.
- Eşleme/sürüm kabulde pin'lenir, DB'de değiştirilemez. CSV checkpoint'i başlıktan
  sonraki veri satırıdır; JSONL checkpoint'i fiziksel satırdır. Özgün kaynak
  yeniden okunarak checkpoint'e kadar atlanır. CSV v1 tek fiziksel satırlı
  kayıtları destekler; çok satırlı hücreler desteklenmez.
- Bilinmeyen sütun eşlemesi/kayıt ailesi/sürüm açık hata verir. Hatalı ölçüm
  satırları ayrı kalite kaydı olur; eksik değer sıfıra çevrilmez. CSV başlık
  hatası veya kaynak sınırı aşımı hiçbir staging'i yayımlamaz.

Anket, meteoroloji, bölgesel tüketim ve diğer araştırma kayıtlarının yayın
yolları henüz uygulanmadı. Veri seti seçimi ve sentetik üretici kalibrasyonu
[araştırma başlangıç planındaki](research-data-bootstrap.md) sonraki iştir.

## Bu teslimatın doğrulaması

29 Ekim 2026: `pytest -q` **187 geçti, atlanan test yok**; Ruff lint/format,
`uv lock --check` ve `git diff --check` geçti. Yerel geliştirme DB'sine yalnız
ileri yönlü `005_source_adapters.sql` migration'ı uygulandı; veri sıfırlanmadı.

`prove_source_adapters.py` gerçek CLI → yerel HTTP → ayrı PostgreSQL yolunda
iki özgün nesne ve üç kanonik ölçümle 19,75 litrenin korunduğunu, tekrar
teslimatın yeni iş açmadığını ve dataset doğrulamasını gösterdi.
Bu koşumun kaydı Git dışındaki
`var/proofs/source-adapters/face170bbee0496c8d36dc036f56f752/proof.json` dosyasıdır.
Kanıt DB'si script sonunda kaldırılmıştır; rapor ve dondurulmuş dosyalar kalır.
Bu sonuç gerçek sağlayıcı bağlantısı, kamuya açık veri setinin semantik eşlemesi,
model başarısı veya kapasite ölçümü değildir.
