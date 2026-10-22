# İlk altyapı paketi — doğrulama kaydı

Tarih: 20 Ekim 2026. Windows / Python 3.12.14 / PostgreSQL 18.6.
Ortam: ayrı yerel sentetik küme, 127.0.0.1:55432. Mevcut Windows PostgreSQL
servisi kullanılmadı veya değiştirilmedi.

## Otomatik testler

`uv run pytest -q`: **43 passed**, 26,45 saniye, atlanan test yok.
`uv run ruff check .`: başarılı.
`uv run ruff format --check .`: 30 dosya uygun.
`git diff --check`: başarılı.

Test kapsamı:

- Sözleşme: bilinmeyen alanlar, negatif/NaN değer, kesirli pulse, hatalı revision,
  offsetsiz/numerik zamanlar ve uyumsuz birimler reddedilir; raw hassasiyet korunur.
- Gerçek PostgreSQL: temiz kurulum, sürüm yükseltme ve uygulanmış migration değişikliği kontrolü.
- Aynı dosya ve örtüşen farklı dosya: ölçüm çoğalmaz; gerçekten farklı pulse kaybolmaz.
- İçeriği değişmiş aynı olay/sürüm ve eksik revision: atomik yayın durur.
- Geç/düzeltilmiş veri: güncel özet yenilenir; dondurulmuş dataset değişmez.
- Karantina: bilinmeyen stream çözüldükten sonra açık retry ile kaydı geri kazanma.
- Worker: checkpoint, eski token'ın reddi, iki worker'ın aynı işi alamaması,
  son denemede çöken işin terminal duruma geçmesi.
- Ayrı OS sürecini zorla kapatma ve gerçek worker komutuyla yeniden başlatma.
- Kaynak bozulması, path kaçışı ve dosya limitleri.
- Gece yarısı, ölçüm zamanındaki kurulum/üyelik, 23/25 saatlik DST günleri.
- Kümülatif endeks sıfırlanması; dönemin saatlere uydurma şekilde dağıtılmaması.
- DB katmanında eksik pulse katsayısı ve çakışan üyelik/kurulum engelleri.
- Ayrı boş DB'ye yedek geri dönüşü: tüm tablo sayıları, kaynak checksum'ları,
  tüketim sonuçları ve dondurulmuş dataset eşleşmesi.
- Bozuk yedeğin DB'ye yazılmadan reddi; aktif DB/storage üzerine restore engeli.

## Kullanıcı akışı ve notebook

`scripts/prove_infrastructure.py` gerçek CLI/worker süreçlerini, notebook kernel'ini,
pg_dump ve pg_restore'u kullanarak ayrı yeni DB'lerde tamamlandı. Model veya kapasite
başarısı ölçülmedi. İspat DB'leri bu scriptin kendi oluşturduğu geçici DB'lerdi;
doğrulama sonunda kaldırıldı, dosyaları tutuldu.

Son başarılı rapor: `var/proofs/97c1aaaa7e2e/report.json`.
Çalıştırılmış notebook: `var/proofs/97c1aaaa7e2e/executed_notebook.ipynb`.

| Ölçüm | Sonuç |
|---|---|
| Pulse sayısı | 2.208 |
| Toplam gözlenen hacim | 220,8 L |
| Günlük satır | 4 |
| Saatlik satır | 96 |
| Hane-gün toplamları | 54 / 54 / 56,4 / 56,4 L |
| Aynı kaynağı ikinci kabul | duplicate=true, hacim değişmedi |
| Notebook | Hatasız çalıştı; core fonksiyonlarını çağırdı |
| Yedekten sonuç karşılaştırması | Eşleşti |
| Yedekten dataset doğrulaması | Başarılı |

Tekrar çalıştırmak için:

```powershell
.\.tools\bin\uv.exe run --extra notebooks python scripts/prove_infrastructure.py
```

## Kullanıma bırakılan yerel örnek

`var/fixtures/first/measurements.jsonl` kaynak dosyası asıl sentetik geliştirme DB'sine
de yüklendi. Kaynak ID: `b92ce3c4-38d1-4919-a631-5c5569ca5dde`.
Dataset ID: `1c19ffbc-aa12-44a7-9d4a-c87e0590ce87`.
Dataset dizini: `var/synthetic/datasets/1c19ffbc-aa12-44a7-9d4a-c87e0590ce87`.

Yerel PostgreSQL gerçekten durdurulup yeniden başlatıldı. Sonrasında 4 günlük satır,
220,8 L toplam ve dataset checksum/manifest doğrulaması korundu. Küme kullanıcı
inceleyebilsin diye çalışır durumda bırakıldı.

Yedek: `var/backups/initial-infrastructure-20261020`.
Yedek 2.208 measurement / 2.208 provenance / 1 kaynak / 1 dataset ve 3 migration
içerir; staging tabloları boştur. Kaynak ve dosya alanları `.gitignore` kapsamındadır.

## Doğrulanmamış kapsam

Compose konfigürasyonu bu bilgisayarda Docker olmadığı için çalıştırılmadı.
GitHub Actions tanımı hazırlandı; remote push/CI çalıştırması yapılmadı.
Üç makalenin kapsamlı sentetik deneyleri, gerçek veri adaptörü, kurumda erişim/retention,
kapasite benchmark'ı ve panel bu ilk paketin tamamlanmış çıktıları değildir.
Araştırma protokollerinde durum açıkça `planned-not-executed` olarak kayıtlıdır.

Commit ve push yapılmadı. Kimlik bilgileri veya çalışma verileri Git'e eklenmedi.
