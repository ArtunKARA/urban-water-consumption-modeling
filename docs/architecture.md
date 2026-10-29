# Mimari ve ilk teslimat sınırı

Durum: ilk altyapı ve sayaç kaynak adaptörleri. Sentetik geliştirme ve ayrı
açık araştırma ortamı desteklenir; kurumsal dağıtım hazır değildir.

Bu repo; veri sözleşmesi, kaynak saklama, kalıcı veri alımı, kalite, zaman bazlı
işleme ve dondurulmuş araştırma çıktısının ortak çekirdeğidir. Python/CLI/notebook
ilk kullanım yoludur. Panel daha sonraki teslimattır.

Kaynakların reader/normalizer arabirimleri ve yapılandırmadan seçimi için
[değiştirilebilir kaynak tasarımı](source-adapters.md) uygulama geçişini tarif eder.
Dosya/tek HTTP yanıtı ve JSONL/eşlemeli CSV sayaç yolu uygulanmıştır;
[kullanım kapsamı](source-adapters-usage.md) diğer kayıt ailelerinden ayrıdır.

## Bağımlılık ve veri akışı

```text
contracts <-- core <-- cli / worker / notebooks
    ^                 ^
    +-- simulator ----+ (yalnızca CLI üzerinden kaynak dosyası üretir)

dosya / HTTP nesnesi -> sıkıştırılmış özgün baytlar + SHA256 + sabit eşleme
              -> source + kalıcı job
              -> doğrulama + parçalı staging + checkpoint
              -> tek transaction ile measurement/provenance/quality yayını
              -> gözlenen saatlik/günlük hacim
              -> değişmez Parquet + manifest + dataset kaydı
```

Core simulator'ü veya truth dosyasını import etmez. Notebook iş mantığı barındırmaz.
Bir workspace ve uv.lock kullanılır. Bu ilk paket model eğitimi, FastAPI ve panel
gerektirmez; ağır ML bağımlılıkları sonraki deney paketleriyle gelir.

## Veri sahipliği

- `registry`: study/cohort/membership/household/meter/stream/installation ilişkileri.
- `ingestion`: source/job/staged_measurement/measurement/provenance/quality tabloları.
- `processing`: bu kayıtları okur; ham ölçüm güncellemez. Şimdilik sonuçlar anlık
  hesaplanır; düzeltme geldiğinde eski bir önbelleği yanlışlıkla sunma riski yoktur.
- `datasets`: dondurulmuş veri seti dosyaları, manifest ve kaynak bağımlılıkları.
- `backup`: tutarlı PostgreSQL snapshot'ı ve ona bağlı dosyaların kopyası.

Kaynak JSON satırı her ölçümde tekrar saklanmaz. Özgün kaynak dosyası ve satır numarası
korunur. Kanonik olay bir kez, her teslimatın kökeni provenance üzerinden tutulur.
İlk sürüm native PostgreSQL'dir. Partition/TimescaleDB kararı hacim ve indeks/WAL
ölçümlerinden sonra verilir; erken partition global olay tekilliğini bozmamalıdır.

## Değişmezler

1. Dosya kalıcılaştırılmadan DB kabulü yapılmaz. DB kaydı başarısızsa kalan sahipsiz
   dosya güvenlidir; otomatik silme yoktur. Manifestli kaynak olmadan yayın yapılmaz.
2. Yayın öncesi staging araştırma sorgularında görünmez. İş ortasında kapanışta
   checkpoint son commit edilen satırı gösterir. Tekrar başlatma dosyayı baştan
   okuyup checkpoint'e kadar atlar; ileride indeksli kaynak biçimiyle optimize edilebilir.
3. İş teslimi en az bir kezdir. Token ve kilit kontrolü eski worker'ın yeni sahibin
   yerine yazmasını engeller. Her uzun aşama lease süresiyle uyumlu olmalıdır.
4. Aynı kaynak SHA256+sözleşme+okuyucu sürümü ikinci iş oluşturmaz. Aynı olay/sürüm
   başka dosyada gelirse ölçüm çoğalmaz. Aynı anahtarın değişmiş içeriği çatışmadır.
5. Düzeltme açık revision ile gelir; geçmiş sürümler korunur. İlk sürüm 1 ve sonraki
   sürümler kesintisizdir. Eksik önceki sürüm geldiğinde başarısız kaynak yeniden denenebilir.
6. Sayaç/stream konfigürasyonu zaman aralıklıdır; bir sayaçta çakışan alternatif
   ölçüm temsilleri kabul edilmez. Kurulum aralıkları `[başlangıç, bitiş)` biçimindedir.
7. Pulse, dönem hacmi ve kümülatif endeks ayrı anlam taşır. Negatif endeks farkı
   sıfıra çevrilmez. Dönem toplamı saatlere eşit dağıtılmaz.
8. Hane ataması ölçüm zamanına göre yapılır. Kurulum değişimini aşan dönem hacmi
   tahmini olarak hanelere bölünmez. Atanamayan hacim raporda görünür.
9. Ölçümün olmaması sıfır veya cihaz kesintisi değildir. Coverage varsayılan `unknown`.
   Günlük değerler **gözlenen hacimdir**, tam günlük tüketim iddiası taşımaz.
10. Dondurulmuş dataset güncel ölçümler değişince değişmez; manifest kod ve bağımlılık
    hash'ini, kaynakları ve hane atamasında kullanılan registry bilgisini içerir.
11. İşin başarılı çalışması analizin yeterli veriyle sonuç ürettiği anlamına gelmez:
    worker `succeeded`, `partial`, `insufficient_data`, `failed_attempt`, `lease_lost`
    sonucunu bildirir. Kalıcı job state ile veri yeterliliği ayrıdır.
12. Yayınlanmış kısmi kaynağın yeniden işlenmesinde mevcut sonuç ve kalite, yeni yayın
    tamamlanana kadar korunur. Başarısız yeniden işleme eski yayını kaldırmaz.

25 Ekim sağlamlaştırması: Konfigürasyonlar ayrı değişmez sürümlere taşındı; işler
bu sürümleri pin'ler, ölçümler kullanılan sürüme FK ile bağlıdır. Dataset v2 gözlenen
özetin yanında normalizasyon ve revision kökenini Parquet olarak dondurur. Cohort
kapsamı özet, örneklem sayısı, metadata ve kaynak bağımlılıklarına uygulanır. Uzun
I/O'da fenced heartbeat ve worker'da DB yeniden bağlantı davranışı vardır. On
inceleme bulgusunun kabul kanıtı `docs/audit-fixes.md` içinde kayıtlıdır.

## Ölçek sınırları ve sonraki geliştirme

Kaynak başına varsayılan 256 MiB, satır başına 64 KiB, 2 milyon satır ve bir sorguda
1 milyon özet kovası sınırı vardır. Bunlar güvenlik/bellek bütçeleridir; bilimsel
örneklem büyüklüğü değildir. Büyük dönemler birden çok kaynak paketi olarak gelir.
Özgün kaynaklar gzip, dondurulmuş özetler Parquet/zstd'dir. Hacimler Decimal ile
işlenir; desteklenen hassasiyet dışında değer sessizce yuvarlanmaz.

İlk worker CPU/DB işlemini aynı süreçte yürütür; staging'e COPY ile toplu yazar.
Yük altında lease/heartbeat ve toplu yükleme performansı ayrıca ölçülmelidir.
Eğitim işlemleri worker'ın ingestion
transaction'ına konulmayacak; dondurulmuş veri üzerinden ayrı deney işi olacaktır.

Gerçek kaynak örneği gelmeden Manas/İSU alanları tahmin edilmez. Gerçek adaptör;
olay anahtarı, cihaz yeniden başlatma dizisi, zaman dilimi, birim, endeks/pulse
ilişkisi, düzeltme ve yeniden gönderim semantiği kanıtlandıktan sonra eklenecek.

İlk DB yerel sentetik geliştirme hesabıyla çalışır. Kurumda gerçek veriye geçişin
giriş koşulları: yetkilendirilmiş ortam, ayrı DB/depolama/hesaplar, sınırlı runtime
ve okuma rolleri, saklama/geri çekilme işlemleri ve bağımlı dataset geçersizleştirmesi,
PII dışlama ve sağlayıcı örnekleriyle sözleşme testleridir. Bu sürüm bunları tamamlanmış
kurumsal dağıtım olarak sunmaz. İSU kimlik eşleştirme anahtarı araştırma DB'sine gelmez.

## Teslimat sırası

1. Bu paket: kurulum, sözleşme, registry, ingestion, gözlenen hacim, snapshot, kurtarma.
2. Kapsamlı sentetik paket: fiziksel kullanım -> sensör -> aktarım hatası katmanları;
   ayrı doğruluk, araştırma ve kapasite senaryoları; üç makale protokolünün çalıştırılması.
3. Sağlayıcı örneğiyle gerçek adaptör ve kurum ortamı doğrulaması.
4. Araştırmacı/danışman/yetkili İSU uzmanı için panel ve erişim akışı.

İlk kapsamlı sentetik teslimat üç araştırma hattını birlikte kapsar. İlk altyapı
paketinin geçmesi bu üç makalenin deneylerinin veya bilimsel başarısının tamamlandığı
anlamına gelmez. Protokollerin tek kaynağı `research/experiments/protocols.toml` dosyasıdır.
