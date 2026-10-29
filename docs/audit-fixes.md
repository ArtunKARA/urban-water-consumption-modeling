# On inceleme bulgusu — düzeltme ve doğrulama

Tarih: 25 Ekim 2026. Önceki inceleme: `var/audits/70893fd9ec/review.md`.
Kapsam: rapordaki F01-F10. Bu paket kapsamlı davranış simülatörü, gerçek veri
adaptörü veya makale deneyleri eklemez.

## Sonuç

On bulgunun düzeltmeleri uygulandı. Gerçek PostgreSQL üzerinde **72 test geçti**,
skip yok; son tam koşum 57.21 saniye. Önceki 43 test korunarak karşı örnekleri
kapsayan 29 ek test vakası eklendi. Ruff ve format kontrolü başarılı; uv.lock güncel;
git diff --check başarılı. İncelenen senaryolar için kabul kanıtıdır; bütün olası
üretim arızalarının veya gerçek veriyle bilimsel başarının garantisi değildir.

## Bulgu karşılıkları

| Bulgu | Uygulanan düzeltme | Kalıcı kabul kanıtı |
|---|---|---|
| F01: Bozuk dataset ile başarılı restore | Backup öncesi, kopyalama sonrası ve restore sonrası DB manifest'iyle Parquet/JSON/normalizasyon doğrulaması; ham kaynak kopyası da tekrar doğrulanır. | `test_f01_*`: bozuk Parquet, manifest, normalizasyon dosyası; dış taşıma hash'i yeniden hesaplanmış bozuk yedek; kopyalama sırasında bozulma. |
| F02: Katsayının chunk'lar arasında değişmesi | Değişmez `stream_configuration`, job pin'leri, measurement/staging config FK'si ve DB normalizasyon koruması. Kullanılmış config yerinde değişmez; yeni sürüm API ile eklenir. | `test_f02_*`: eski pin ile iki pulse 0.2 L; frozen config ve Parquet ile aynı hesap; yeni olay revision'ı, zaman bazlı sürüm seçimi, hassasiyet ve legacy backfill. |
| F03: Yanlış dönem sınırı | Pulse anı bitiş hariç; interval'in tamamı tek config döneminde olmalı; interval ve kapanış endeksi bitişe eşit olabilir. | `test_f03_*`: tam bitiş sınırı, erken başlangıç/geç bitiş reddi, pulse ve endeks farkı. |
| F04: Kayıtlı hane sayısının artması | Üyelik sorgusu yan etkisiz; yalnız cohort sayaçları aday gözlem sorgusuna alınır. | Tek haneli cohort'ta kayıtlı/temsil edilen hane sayısı 1. |
| F05: İlgisiz kaynak ve metadata | Kaynak kökeni seçili sayaçların aday ölçümlerinden ve revision atalarından çıkarılır; registry/üyelik seçili cohort ile sınırlı. | İlgisiz cohort kaynağı bozulsa da freeze çalışır; karma paket korunur; gerekli eski revision kaynakları kaybolmaz. |
| F06: Uzun I/O'da lease tükenmesi | Checksum ve checkpoint taraması boyunca bağımsız fenced heartbeat; eski/bitmiş token yenilenemez. | Yavaş checksum, uzun replay taraması, tamamlanmış ve başka workera geçmiş token testleri. |
| F07: Geçici DB hatasında worker'ın kapanması | Claim/işleme/fail bağlantı hataları temiz durumla döner; sürekli worker en çok 30 s backoff ile yeniden bağlanır. | Claim ve fail sırasında hata enjeksiyonu; sonraki işin tamamlanması, attempt sayısı ve worker bekleme dizisi. |
| F08: Kolon eklenince COPY bozulması | COPY ve kalite yayınında açık kolon listeleri. | Staging ve staged quality tablolarına default'lu kolon eklenince valid/invalid satırların işlenmesi. |
| F09: Eski numaralı migration'ın uygulanması | Bilinen migration'lar dosya listesinin kesintisiz öneki olmalı; sıra ve checksum ön kontrolleri yeni DDL'den önce yapılır. SQL checkout LF sabitlendi. | 000 numaralı sonradan ekleme herhangi bir tablo oluşturmadan reddedilir; mevcut yükseltme/checksum testleri de geçer. |
| F10: Aynı gün için iki DST kovası | Gün sınırı yerel takvim tarihinden üretilir; tekrar eden gece yarısında ilk an, atlanan gece yarısında ilk gerçek an seçilir. | Havana'da iki 00:30 aynı 25 saatlik güne; Santiago'da olmayan gece yarısı; mevcut Berlin/İstanbul kontrolleri. |

Regresyonlar: `tests/test_audit_regressions.py`. Eski token kontrolü ayrıca
`tests/test_pipeline.py` içindeki crash/resume testine eklendi.

## Şema ve veri sözleşmesi

Yeni migration: `004_versioned_stream_configuration.sql`. 001-003 dosyaları değiştirilmedi.
Mevcut ölçümler config sürümüne bağlandı; pulse/endeks ham değeri ve litre değeri
değiştirilmedi. Eski hesap mevcut legacy ayarla çelişirse migration bunu otomatik
uydurmak yerine durdurur. Config payload'ı ve stream kimliği DB trigger'larıyla korunur.

Yeni dataset v2, `observed_volume.parquet` yanında `normalization.parquet` içerir.
Ölçüm -> config sürümü bağı ve gerekli revision geçmişi dondurulur. `is_current`
revision seçimini belirtir; üyelik/kurulum/kalite ve endeks farkı kuralları ayrıca uygulanır.
Eski v1 dataset'lerin doğrulaması devam eder. Ayrıntılar `docs/data-dictionary.md` içinde.

## CLI / notebook / kurtarma kanıtı

`uv run --extra notebooks python scripts/prove_infrastructure.py` başarılı.
Rapor: `var/proofs/83c3c578c5ee/report.json`.
Çalıştırılmış notebook: aynı dizindeki `executed_notebook.ipynb`.
Kod hash'i: `d2833325d1fdc316d063b7c3568e1502fcc3ba0f2429cd93cb6ad32afa01af6f`.

- 2.208 pulse = 220.8 L; 4 günlük, 96 saatlik satır.
- Aynı kaynağı ikinci kabul hacmi artırmadı.
- Dataset v2, normalizasyon dosyası ve manifest doğrulandı.
- Yeni boş DB ve ayrı storage'a geri dönüşte sonuçlar ve dataset eşleşti.
- Proof yalnız kendi oluşturduğu geçici DB'leri kaldırdı; kanıt dosyaları var altında kaldı.

## Mevcut örneğin korunması

Migration öncesi yedek: `var/backups/before-audit-fixes-20261025`.
Migration sonrası yedek: `var/backups/after-audit-fixes-20261025`.

Asıl yerel örnekte 2.208 measurement, 2.208 provenance, 1 kaynak korunuyor.
4 migration, 2 stream config ve eski/yeni 2 dataset var; staging boş.
PostgreSQL yalnız proje kümesinde durdurulup yeniden başlatıldı. Ardından:

- Eski dataset `1c19ffbc-aa12-44a7-9d4a-c87e0590ce87`: doğrulandı.
- Yeni v2 dataset `4b27876b-9ed8-4b8f-8fd1-87b0b6e41531`: doğrulandı.
- Günlük özet: yine 4 satır / toplam 220.8 L.

Yerel küme inceleme için çalışır durumda. Başka PostgreSQL servisi değiştirilmedi.
Commit/push yapılmadı. Docker/uzak CI ve gerçek ağ kesintisi bu tur çalıştırılmadı;
DB yeniden bağlanma yolları kontrollü hata enjeksiyonuyla, worker kesintisi mevcut
ayrı OS süreci testleriyle sınandı.
