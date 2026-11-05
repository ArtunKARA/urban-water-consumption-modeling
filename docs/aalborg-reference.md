# İlk açık araştırma verisi: Aalborg v1

4 Kasım 2026. Yayımlanmış veri, yerel dosya → adaptör → ortak veri katmanı →
saatlik/günlük dataset yoluna bağlandı. Ardından ayrı protokolle
[ilk günlük tahmin deneyi](daily-forecast-reference.md) çalıştırıldı.

Kaynak: Schaffer ve diğerleri (2025),
[Residential Household Dataset](https://zenodo.org/records/15180141), sabit v1,
DOI `10.5281/zenodo.15180141`; lisans metadata'dan **CC BY 4.0** olarak doğrulandı.
[Teknik raporun](https://doi.org/10.54337/aau780546283) PDF'si HTTP 403 döndürdü.
Zaman/ön işleme anlamları, aynı paketteki `00_save_swm_for_app.R`,
`04_write_final_data.R`, `02_write_final_data.R` dosyalarından doğrulandı.
İndirilen kod çalıştırılmadı.

## Kullanılacak yerler

| Yer | İçerik |
|---|---|
| [Referans kataloğu](../research/reference_data/aalborg-v1.json) | Sürüm/lisans, sabit ZIP/CSV ve işleme kodu hash'leri, sınırlar |
| `var/research/aalborg/v1/raw/` | Özgün ZIP, seçili CSV, metadata, indirme zamanı, ilgili R dosyaları; Git dışı |
| `var/research/aalborg/v1/sources.toml` | **Veri noktası:** kullanılan CSV yolu, adaptör, namespace ve köken bilgileri |
| `var/research/aalborg/v1/registry.json` | 10 bina takma adı ile mantıksal ölçüm serisi ilişkileri |
| `var/research/aalborg/v1/source-audit.json` | Kaynak toplamları, hane dönemleri, kısmi günler ve DST |
| `var/research/aalborg/v1/import-report.json` | Sonuçlar ve son Parquet dosyalarının yolları |
| `var/research/aalborg/v1/research.env` | Özel araştırma bağlantı ayarları; parola içerir, Git dışı |
| [İnceleme notebook'u](../notebooks/02_aalborg_reference.ipynb) | Snapshot'lardan örnek satırlar ve toplam/takvim kontrolleri |

DB: `urban_water_aalborg_research`; study: `aalborg-15180141`; cohort:
`daily-2022`; zaman dilimi: `Europe/Copenhagen`. Ayrı DB ve depolama kökü,
projeye ait aynı yerel PostgreSQL sürecini kullanır. Sentetik DB'ye veri yazılmaz.

## Çalıştırma

Repo kökünden:

```powershell
.\.venv\Scripts\python.exe scripts/dev_database.py start
.\.venv\Scripts\python.exe scripts/run_aalborg.py
```

İlk koşum küçük ZIP'i ve yöntem kanıtı R dosyalarını indirir. 745 MB arşivin
tamamı yerine seçili üyeler HTTP Range ile alınır ve sabit SHA256'larla doğrulanır;
büyük arşivin tamamının MD5'i yeniden hesaplanmış sayılmaz. İlgisiz ZIP üyeleri
açılmaz. CSV özgün ZIP üyesinin baytlarıyla saklanır.

Sonraki koşum: `python scripts/run_aalborg.py --offline`. `--prepare-only`
indirme/doğrulama, registry/kaynak ayarı ve kaynak denetimini DB'ye bağlanmadan
hazırlar. Offline modda eksik/değişmiş cache açık hatadır. Runner DB sıfırlamaz;
tekrar teslimatı denetler ve yeni snapshot üretir. Var olan, sahiplik işareti
bulunmayan araştırma DB'sini sahiplenmez. Yarım import başarı diye raporlanmaz.

Ortak CLI'da ortamı açıkça seçin:

```powershell
.\.venv\Scripts\uw.exe --env-file var/research/aalborg/v1/research.env collect meter_readings --config var/research/aalborg/v1/sources.toml
.\.venv\Scripts\uw-worker.exe --env-file var/research/aalborg/v1/research.env --once
.\.venv\Scripts\uw.exe --env-file var/research/aalborg/v1/research.env summarize --grain day
```

Kabuktaki `UW_*` değişkenleri env dosyasından önceliklidir. Runner kendi açık
Settings nesnesini kullanır. Özgün dosyalar değiştirilmez; uyarlama yeni adaptör/
deney sürümüdür. `expected_sha256` CSV'yi kabul sırasında da kontrol eder.
Bu referansın üretilmiş `sources.toml` ve `registry.json` dosyaları da sabitlenir.
Alternatif dosya/API deneyi için yeni bir TOML oluşturup `uw collect` kullanın;
`run_aalborg.py` sabit yayımlanmış referansı tekrar üretir.

## Alan anlamları

| Kaynak | Ortak karşılığı |
|---|---|
| `building` | Bina kodu → `AALBORG-D-01`…`10` hane takma adları ve ayrı saatlik stream'ler |
| `date_time` | Önceki saatin UTC hariç bitişi. Başlangıç bir saat önce; DST'de UTC süresi korunur. |
| `water_use_l` | Ön işlemden geçmiş saatlik litre aynen korunur; `kind=interval`, `unit=L`. |
| `date` | Öğleden öğlene doluluk etiketi günü; tüketimin takvim günü olarak kullanılmaz. |
| `occ_01`…`occ_05` | Tüketimden üretilmiş etiketler; ölçüm/özellik çıktısına alınmaz. |
| Geçmiş teslimat saati | Yayımlanmamış. `received_at`, bu adaptörde gerçek araştırma arşivi alım anıdır; `receipt_basis=research_archive_import`. |

Sayaç seri numarası yayımlanmadığından `logical-meter` registry kodları fiziksel
kimlik iddiası taşımaz; kaynak ölçüm serisini bina takma adına bağlar. Olay
kimliği bina + UTC dönem bitişinden gelir; değer/satır sırası kimliğe dahil değildir.

## Gerçek aktarım sonucu

- 10 hane, **87.360 saatlik kayıt**; hane başına 8.736 kayıt.
- Kaynakta **915.140 litre**; hem saatlik hem günlük ortak katman toplamı aynı.
  Bu, işlenmiş serinin toplamıdır. **38.025 sıfır hacimli saat** aynen korundu.
- **87.360 saatlik**, **3.650 günlük** çıktı satırı; karantina 0, atanamayan
  hacim 0. Tekrar teslimatta kayıt/hacim artışı olmadı; iki snapshot doğrulandı.
- Hane başına **363 tam takvim günü** ve iki kısmi sınır günü var: ilk gün
  13, son gün 11 saat. Bunlar tam günlük hedef değildir.
- 27 Mart 2022 günü 23, 30 Ekim 2022 günü 25 saat; yerel gün birleşiminde
  ölçümler korunur. Kaynağın doluluk `date` etiketi kullanılmaz.
- Coverage `unknown` kalır: upstream interpolasyonla dolu saat ızgarası,
  cihazın kesintisiz ölçtüğünü kanıtlamaz.

## Model çalışmasının sınırı

Yıllık seri endeks interpolasyonu, litreye yuvarlama/fark alma ve günlük minimum
saatlik değerin çıkarılmasını içerir. Bu geriye dönük işlemlerde günün sonraki
saatleri etkili olabilir. İlk tahmin deneyi **yayımlanmış işlenmiş seri üzerinde
geriye dönük karşılaştırma** olarak tanımlanmalı. Gerçek zamanlı ham tüketim veya
kaçak başarısı için uygun ham veri ve yalnız geçmişe bakan ön işleme ayrıca gerekir.

Tam gün seçimi ve zaman sıralı train/validation/test ayrımı,
[ayrı günlük deneyde](daily-forecast-reference.md) uygulandı. 2026'daki arşiv
alımı 2022'deki sağlayıcı teslimat saatini kanıtlamaz; 18:00/23:00 deneyi açık
bir erişim varsayımıyla yapıldı. Anket, gelir, hava, doğrulanmış kaçak etiketleri bu
kaynaktan türetilmiş sayılmaz. Ayrı 8 binalı günlük/doluluk kohortu bu ilk yıllık
kohorta katılmadı.

## Doğrulama kaydı

4 Kasım 2026: **206 test geçti, atlanan test yok**. Ruff lint/format, lock ve
diff kontrolleri geçti. Kaynak hem ilk indirme hem offline tekrar yolunda
hazırlandı; ortak CLI, araştırma env dosyasıyla tekrar teslimatı `duplicate=true`,
worker'ı `idle` olarak bildirdi. İnceleme notebook'unun dört kod hücresi gerçek
snapshot'larla hatasız çalıştı; çıktılı kopya Git dışındaki
`var/research/aalborg/v1/notebook-executed.ipynb` dosyasındadır.
