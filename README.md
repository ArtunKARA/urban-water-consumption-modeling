# Urban water consumption modeling

Yüksek çözünürlüklü sayaç verileriyle açıklanabilir ve doğrulanabilir kentsel su
araştırmaları. Bu ilk paket, **sentetik verinin alınması, saklanması, işlenmesi ve
araştırma çıktısının tekrar üretilebilmesi için çalışan altyapıdır**.

## Bu bilgisayarda hızlı başlangıç — PowerShell

Repo kökünde çalıştırın. Python 3.12, uv ve PostgreSQL 18 araçları gerekir.
Bu bilgisayarda uv repo içinde `.tools/bin/uv.exe` olarak hazırlanmıştır; başka
makinelerde uv kurulumu sonrasında aynı komutlar `uv` ile çalıştırılabilir.

```powershell
$uv = '.\.tools\bin\uv.exe'
& $uv sync --locked
& $uv run python scripts/dev_database.py start
& $uv run uw migrate
& $uv run uw seed --households 2
& $uv run uw simulate var/fixtures/first --households 2 --days 2 --seed 42
& $uv run uw ingest var/fixtures/first/measurements.jsonl
& $uv run uw-worker --once
& $uv run uw summarize --grain day
& $uv run uw summarize --grain hour
& $uv run uw freeze --grain day
& $uv run uw status
```

`simulate` var olan dizinin üzerine yazmaz; yeni deneme için yeni dizin adı kullanın.
Varsayılan doğruluk örneğinde 2.208 pulse / 220,8 litre vardır. Hane-gün toplamları
54 / 54 / 56,4 / 56,4 litredir. Bu küçük örnek gerçekçi davranış veya model başarısı
iddiası taşımaz. Truth ayrı dosyadadır; core tarafından okunmaz.

`uw-worker` parametresiz çalışırsa kalıcı kuyrukta yeni işleri bekler; Ctrl+C ile durur.
Batch ve sürekli çalışan worker aynı işleme kodunu kullanır. Yeni paket kabulü
`uw ingest DOSYA` ile yapılır. Yapılandırılan yerel/HTTP sayaç kaynağı için
`uw collect` ve [çalışan kaynak örnekleri](docs/source-adapters-usage.md) vardır;
sağlayıcıya özel gerçek API/cihaz adaptörü bu sürümün kapsamına dahil değildir.

```powershell
# Aynı dosya ikinci kez kabul edildiğinde duplicate=true; tüketim artmaz.
& $uv run uw ingest var/fixtures/first/measurements.jsonl
# Başarısız veya karantinalı kaynak için düzeltme sonrası açık tekrar deneme:
& $uv run uw retry KAYNAK_UUID
& $uv run uw-worker --once
# Çalışan süreci değil, yalnızca bu projenin yerel PostgreSQL kümesini durdurur:
& $uv run python scripts/dev_database.py stop
```

Yerel küme 127.0.0.1:55432 üzerinde, var/dev-postgres altında çalışır. Mevcut
PostgreSQL Windows servisine bağlanmaz. Otomatik üretilen kimlik bilgileri `.env`
ve var altındadır; Git tarafından izlenmez. Oturum kapatınca uygulama verileri kalır;
sonraki kullanımda `start` yeterlidir. Başlangıç scripti Windows'ta pencere açmadan
yerel PostgreSQL'i başlatır; Windows servisi veya Docker kurmaz.

## Yapı

```text
packages/contracts/   Sürümlü ölçüm sözleşmesi, JSON Schema
packages/core/        Registry, ingestion, kalite, hacim özeti, dataset, backup
packages/simulator/   Bağımsız doğruluk örneği üretimi
packages/research/    İsteğe bağlı öğrenme paketi; dondurulmuş veriden deneyler
apps/cli/             Araştırmacı komutları
apps/worker/          Kalıcı ingestion işlerini yürüten süreç
migrations/           Sıralı, checksum korumalı PostgreSQL migration'ları
research/experiments/ Üç makale için tek otorite deney protokolleri
notebooks/            Çekirdeği çağıran inceleme notebook'u
verification/         Kabul özellikleri ve formal doğrulamanın kapsamı
tests/                Bağımsız beklenen sonuçlar ve gerçek PostgreSQL testleri
docs/                 Mimari, veri sözlüğü ve işletim talimatları
scripts/              Yerel geliştirme yardımcıları
var/                  İzlenmeyen çalışma verileri, kaynaklar, dataset ve yedekler
```

## Kontroller

```powershell
& $uv run python scripts/dev_database.py create-test-db
& $uv run ruff check .
& $uv run ruff format --check .
& $uv run --extra research --extra notebooks pytest -q
```

Testler yalnızca ayrı `_test` isimli DB'deki `uw` şemasını sıfırlar. Bu bilgisayardaki
işaretli yerel kümede `urban_water_test` kullanılır. Başka ortamda açıkça
`UW_TEST_DATABASE_URL` verilmelidir. Test DB belirtilmemişse PostgreSQL testleri
atlanır; bu durum uçtan uca doğrulama yapılmış sayılmaz. Restore testleri yeni geçici
DB oluşturup kendi oluşturdukları DB'yi kaldırır; test hesabında CREATEDB yetkisi gerekir.
Öğrenme testleri `research` extra'sını gerektirir; extra kurulmamışsa bu modül
atlanır. Yukarıdaki tam test komutu bu bağımlılıkları da kurar.

## Notebook

```powershell
& $uv sync --locked --extra notebooks
```

`notebooks/01_observed_volume.ipynb` dosyasını bu `.venv` Python kernel'iyle açın.
Notebook yalnızca core'u çağırır; veri hazırlama mantığı kopyalamaz.

İlk açık araştırma verisini ayrı DB'de hazırlamak ve içeri almak için:

```powershell
& $uv run python scripts/run_aalborg.py
```

[Aalborg veri kaynağı ve sonuçları](docs/aalborg-reference.md), kullanılan dosya
konumlarını ve sınırları açıklar. `notebooks/02_aalborg_reference.ipynb` hazır
saatlik/günlük snapshot'ları inceler. Bu adım model eğitimi içermez.

İlk günlük model karşılaştırmasını çalıştırmak için:

```powershell
& $uv run --extra research --extra notebooks python scripts/run_daily_forecast.py
```

[Günlük tahmin sonuçları ve deney sınırları](docs/daily-forecast-reference.md)
kaydedildi. `notebooks/03_daily_forecast_reference.ipynb` son başarılı koşumu inceler.

[Açık veri kütüphanesi ve eleştirel değerlendirme](docs/public-data-library.md):
11 katalog kaydından 56 özgün dosya indirildi; kaynakları korunarak ayrı Parquet
tabloları hazırlandı. [Kaynak kataloğu](research/reference_data/public-source-catalog.v1.json)
ve [51 alanın kaynak karşılıkları](research/reference_data/public-data-field-coverage.v1.json)
lisans, birim, zaman, kapsam ve etiket sınırlarını kaydeder. Yeni kaynakların
kanonik eğitim adaptörleri bu inceleme katmanından sonra tamamlanacaktır.

[hihAigua kaynak adaptörü](docs/hihaigua-reference.md), 10 hanenin 20 ayrı su
akışını gerçek dosyalardan hazırlıyor. Birim kanıtı eksikken kanonik yayını kapalı
tutuyor; `notebooks/04_hihaigua_reference.ipynb` kaynak/kalite incelemesini açar.

[Günlük sayaç hedefi incelemesi](docs/hihaigua-counter-days.md), UTC günlerinde
endeksten tek değer/aralık çıkarıyor; eksik gün veya sınır tüketimi uydurmuyor.
`notebooks/05_hihaigua_counter_days.ipynb` sonuçları ve hane grafiğini gösterir.

## Belgeler ve kapsam

- [Kaynak → veri → ilişki → model → çıktı diyagramları — v0.1 taslak](docs/contracts/research-data-model.md)
- [Model seçerek renkli akışları incele](docs/contracts/model-flow-map.html)
- [Tüm model akışları tek grafikte — SVG](docs/contracts/model-flow-overview.svg) · [PNG](docs/contracts/model-flow-overview.png) · [A3 PDF](docs/contracts/model-flow-overview.pdf)
- [Model başına kaynak, alan ve ilk yöntem adayları](docs/contracts/model-flow-map.md)
- [İlk model eğitim planı ve deney kartları — taslak](docs/contracts/model-training-plan.md)
- [Veri/model akış haritası denetimi ve düzeltmeler](docs/contracts/model-flow-audit.md)
- [Araştırma girdi/çıktı JSON sözleşmesi — v0.1 taslak](docs/contracts/research-data-contract.v0.1.schema.json)
- [Mimari ve değişmezler](docs/architecture.md)
- [Değiştirilebilir kaynaklar ve adaptör arabirimleri — tasarım](docs/source-adapters.md)
- [Dosya/HTTP kaynak noktaları — çalışan kod ve kullanım](docs/source-adapters-usage.md)
- [Sentetik veri → açık araştırma verisi → API geçiş planı](docs/research-data-bootstrap.md)
- [İlk açık araştırma verisi: Aalborg — aktarım, sonuçlar ve sınırlar](docs/aalborg-reference.md)
- [İlk günlük model karşılaştırması — F01 açık veri referansı](docs/daily-forecast-reference.md)
- [İndirilen açık veriler, uygunluk analizi ve hazırlama komutları](docs/public-data-library.md)
- [hihAigua adaptörü — gerçek kaynak hazırlığı, kalite ve birim kapısı](docs/hihaigua-reference.md)
- [hihAigua günlük hedefleri — tek değer, aralık, bilgi zamanı ve sınırlar](docs/hihaigua-counter-days.md)
- [Veri sözlüğü](docs/data-dictionary.md)
- [Yedekleme, kurtarma ve işletim](docs/operations.md)
- [Araştırma protokolleri](research/experiments/protocols.toml)
- [Kabul doğrulaması](verification/README.md)
- [İlk paket doğrulama sonuçları](docs/validation.md)
- [On inceleme bulgusunun düzeltmeleri](docs/audit-fixes.md)

Sağlayıcıya özel gerçek veri adaptörü, kapsamlı davranış simülatörü, üç makalenin model/dağıtık/formal
deneyleri ve panel sonraki teslimatlardır. İlk gerçek veri geçişinden önce kurumun
erişim, saklama ve sağlayıcı sözleşmesi koşulları tamamlanacaktır.
