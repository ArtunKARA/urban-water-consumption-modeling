# Yerel işletim ve kurtarma

## Ortam

Python 3.12; PostgreSQL sunucusu ve eşleşen pg_dump/pg_restore araçları; uv.lock ile
kilitli workspace. CLI ve worker repo kökünde çalıştırılır. Şu an editable workspace
dağıtımı hedeflenir; bağımsız wheel/servis kurulum paketi henüz yayımlanmamıştır.

Windows yerel araçları `scripts/dev_database.py` bulur. Farklı dizin için `UW_PG_BIN`
verilebilir. Varsayılan port 55432'dir; port doluysa script mevcut sunucuya sessizce
geçmez, başlatma başarısız olur. `.env` veya veri dizini varken sahiplik işareti yoksa
üzerine yazmaz. Başlangıçta rastgele parola ve SCRAM kimlik doğrulaması kullanılır.

`UW_ENV=synthetic` zorunlu. `UW_STORAGE_ROOT` bu ortamın özgün dosya ve dataset köküdür.
DB bağlantı bilgileri loglanmaz; ayar nesnesinin repr çıktısında parola görünmez.
Bu yerel ilk paket kurumsal kullanıcı yetkilendirmesi ve üretim dağıtımı değildir.

Docker alternatifinde `.env.example` üzerinden yeni ortam ayarlayın, `UW_PG_PASSWORD`
ve bağlantı URL'sindeki parolayı aynı yapın, `docker compose up -d` çalıştırın. Native
küme ve Compose aynı 55432 portunu kullanır; ikisini aynı anda açmayın. Volume yedek
değildir. PostgreSQL 18 volume yolu `/var/lib/postgresql` olarak tanımlıdır.
Docker bu bilgisayarda mevcut olmadığı için Compose çalıştırılarak doğrulanmamıştır.

## İşler ve hata çözümü

`uw status` kalıcı iş durumunu, deneme sayısını, checkpoint'i ve kalite kodlarını
gösterir. Başarısız işlerde kaynak satırını object_key+row_no üzerinden inceleyin;
özgün dosyayı düzenlemeyin. Değişmiş ölçüm yeni revision/yeni kaynak dosyasıdır.
Başka bir dosya içinde aynı olay/sürüm değişirse bütün kaynak çatışmaya düşer.

Otomatik tekrar denemeler en çok yapılandırılmış attempt sayısıdır, artan bekleme
süresiyle kuyruğa alınır. Kaynak çatışması/limit aşımı terminaldir. Worker kapanırsa
lease bittikten sonra başka worker aynı checkpoint'ten devam eder. Retry komutu
terminal başarısız işe veya kalite sorunu olan tamamlanmış işe yeni deneme hakkı
tanır. Temiz yayımlanmış kaynakta tekrar işlem yeni parser sürümüyle tanımlanmalıdır.

Dosya başına sınır, gzip açılmış özgün bayt sayısı üzerinden denetlenir. Kaynak
önce hash/size ile doğrulanır; işlem sırasında parça parça okunup COPY ile staging'e
yazılır. Checkpoint ve staging aynı transaction'da commit edilir. Dosya doğrulaması
ve checkpoint'e kadar tarama boyunca ayrı heartbeat sahiplik token'ıyla lease'i
yeniler. Süresi dolmuş, değiştirilmiş veya tamamlanmış token yenilenemez. Chunk ve
yayın transaction'ları job satırını kilitler; heartbeat bu kilidin arkasında beklemez.
DB bağlantıları 5 saniyelik bağlantı zaman aşımı kullanır. Sürekli worker geçici
bağlantı hatasında artan, en fazla 30 saniyelik beklemeyle tekrar bağlanır.
`--once` bağlantı hatasını sıfırdan farklı çıkış koduyla bildirir. Varsayılan lease
60 saniyedir; büyük veri/kurum ağı için bağlantı ve işlem gecikmelerini ayrıca ölçün.

Araştırma özetleri varsayılan `synthetic-pilot` study / `fixture` cohort içindir;
`UW_STUDY_CODE` ve `UW_COHORT_CODE` ile seçilir. Membership dönemleri olay zamanında
uygulanır. Üyeliği/kurulum aralığını aşan dönemler tahmini olarak bölünmez.

## Tutarlı yedek

```powershell
$uv = '.\.tools\bin\uv.exe'
& $uv run uw backup var/backups/checkpoint-01
```

Yedek dizini yeni ve aktif storage kökünün dışında olmalıdır. Komut tutarlı DB
snapshot'ını pg_dump'a aktarır; aynı snapshot'ta kayıtlı kaynak dosyalarını ve
dataset/manifest'leri kopyalar. Her dosya checksum ile listelenir. Worker yeni iş
yayımlarken dump'ın DB ve dosya referansları aynı snapshot'a aittir. Kaynak silme
işlemi bu sürümde yoktur. İleride retention/withdrawal ile backup kilidi koordine edilmelidir.

Başarı kanıtı `manifest.json` ile tamamlanmış yedektir. Komut hata verirse kısmi
dizini tam yedek saymayın; yeni denemede yeni dizin kullanın. Canlı pgdata klasörünü
Drive/Git içine senkronize etmeyin. Yedeklerin ayrı disk/kurum alanına taşınması ve
saklama süresi kurum işletim kararlarıdır; aynı disk kopyası disk kaybına dayanmaz.

## Ayrı ortama geri dönüş

```powershell
& $uv run python scripts/dev_database.py create-restore-db
# .env'deki kaynak DSN'yi ekrana yazmadan, yalnızca veritabanı adını değiştirin:
$restoreDsn = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; from psycopg.conninfo import make_conninfo; print(make_conninfo(dotenv_values('.env')['UW_DATABASE_URL'], dbname='urban_water_restore'))"
$env:UW_RESTORE_DATABASE_URL = $restoreDsn
& $uv run uw restore var/backups/checkpoint-01 --storage-root var/restored-checkpoint-01
Remove-Item Env:UW_RESTORE_DATABASE_URL
```

PowerShell değişken ataması DSN'yi görüntülemez; komutu loglayan ortamlarda kurumun
secret yönetimini kullanın. Restore hedef DB adı `_restore` veya `_test` ile bitmeli,
kaynak DB'den farklı ve boş olmalıdır. Hedef storage dizini de boş olmalıdır.
Checksums DB yazımından önce doğrulanır; pg_restore tek transaction kullanır.
Sonuçta tüm uw tablo sayıları, özgün kaynak hash'leri, dataset'in DB manifest'i,
Parquet hash/satır sayısı ve varsa normalizasyon geçmişi doğrulanır. Yedekleme de
dataset'leri hem kaynakta hem kopyalandıktan sonra DB snapshot'ındaki manifest'e
göre denetler. Kopyanın güncel hash'ini almak, özgün dataset bütünlüğünün yerine geçmez.
Test ayrıca gözlenen tüketim sonuçlarını ve dondurulmuş dataset'i karşılaştırır.

Restore mevcut DB/dosya üzerine yazmaz. Kesintili restore'da dosyalar kalabilir;
başarılı sayılmaz. Yeni boş DB/dizinle yeniden deneyin; başarısız alanı incelemeden
silmek yerine koruyun. Manifest bir imza değildir; kasıtlı saldırıya karşı güvenilir
backup deposu ve erişim denetimi gerekir.

## Şema güncellemesi

`uw migrate` sıralı migration'ları tek transaction ve migration kilidiyle uygular.
Uygulanmış dosya checksum'u değişmişse durur. Mevcut migration dosyasını değiştirmeyin;
yeni numaralı migration ekleyin. Uygulanmış migration'lar sıralı dosya listesinin
kesintisiz başlangıcını oluşturmalıdır; eski numaralı ekleme DDL'den önce reddedilir.
SQL dosyaları `.gitattributes` ile LF tutulur. 004 geçişinden önce worker'ları durdurun;
mevcut normalizasyon legacy katsayıyla çelişiyorsa migration geçmişi uydurmak yerine durur.
İlk paket btree_gist gerektirir; kısıtlı kurum
ortamında extension kurulumunu yetkili DB yöneticisi önceden yapmalıdır.

## Sayaç konfigürasyonu

`stream` kimliği ve legacy ilk ayarlar yerinde değiştirilemez. `stream_configuration`
katsayı, tür ve geçerlilik dönemini sürümler. İş ilk claim sırasında etkin sürümleri
sabitler; yeniden başlama aynı pin'leri kullanır. Yeni ayar açık işin anlamını değiştirmez.
API atomik olarak eski etkin sürümleri emekliye ayırır, yeni dönemleri ekler. Örnek:

```python
from datetime import datetime
from decimal import Decimal
from urban_water.registry import ConfigurationPeriod, configure_stream

configure_stream(
    settings,
    "S0001-pulse",
    [
        ConfigurationPeriod(
            valid_from=datetime.fromisoformat("2000-01-01T00:00:00Z"),
            liters_per_pulse=Decimal("0.2"),
        )
    ],
)
```

Bitişli/ardışık dönemler aynı listede verilmelidir; etkin dönemler çakışamaz.
Yeni işler yeni sürümleri kullanır. Yayınlanmış bir olayın hacmini düzeltmek için
açık yeni olay revision'ı gerekir; aynı anahtarı başka katsayıyla yeniden göndermek
eski sonucu sessizce değiştirmez. Kısmi/başarısız kaynakta `retry` staging ve pin'leri
yeniden oluşturur; önceden yayımlanmış ölçümler ve frozen dataset'ler korunur.

Pulse için bitiş hariçtir. Interval'in bütün `[start,end)` dönemi tek konfigürasyona
sığmalıdır; end'in tam config bitişine eşit olması geçerlidir. Kümülatif endekste
bitiş anındaki okuma kapanış referansı olarak kabul edilir; bitişik config'lerde
bu örnek önceki dönemi kapatır. Gece yarısındaki DST tekrarında günlük başlangıç
ilk gerçek gece yarısıdır; olmayan gece yarısında günün ilk gerçek anı seçilir.

## İlk sürümün işletim sınırları

Harici bildirim, kimlik sağlayıcı, şifreli kurum yedek deposu, disk doluluğu alarmı,
kapasite SLA'sı ve otomatik retention kurulmuş değildir. Geliştirme ortamında dosya
boyutu/satır/bucket sınırları ve kalıcı hata kodları vardır. Büyük veri geçişinde
heap+indeks+WAL+geçici dosya+backup kapasitesi ve saatlik/günlük gecikme ölçülecektir.
