# Veri sözlüğü — sayaç ölçümü v1

Sentetik JSONL `synthetic.measurement.v1`, ayrı açık araştırma ortamı
`urban-water.measurement.v1` kullanır. Alan semantiği aynıdır. Yerel CSV ve HTTP
girdilerinin eşlenmesi için [kaynak kullanımına](source-adapters-usage.md) bakın.

Gerçek sağlayıcı sözleşmesi değildir. JSONL UTF-8, satır başına bir nesne. Bilinmeyen
alanlar kabul edilmez. `uw contract` tek otorite Pydantic modelinden JSON Schema üretir.

| Alan | Anlam |
|---|---|
| schema_version | Ortama göre `synthetic.measurement.v1` veya `urban-water.measurement.v1` |
| stream_code | Registry'deki zaman aralıklı ölçüm akışı |
| event_id | Stream içinde kararlı kaynak olay kimliği; zaman+değerden türetilmez |
| revision | Pozitif tam sayı; 1'den başlayan açık düzeltme sürümü |
| kind | `pulse`, `interval` veya `index`; stream türüyle aynı |
| occurred_at | Offset içeren pulse anı veya dönem/endeks bitiş anı |
| received_at | Kaynağın teslim aldığı an; analiz anıyla karıştırılmaz |
| interval_start | Yalnızca interval için dahil başlangıç; occurred_at hariç bitiş |
| value | Pozitif/sıfır kesin ondalık; en çok 24 basamak, 9 ondalık |
| unit | `pulse`, `L`, `m3`; pulse sayısı tam sayı olmak zorunda |

Ölçümün kaynak zamanı, kaynak değeri ve birimi korunur. PostgreSQL zamanları UTC
anları olarak tutar; özetlerin takvim günü `UW_TIMEZONE` üzerinden belirlenir.
Saat kovaları UTC'de birer saatlik aralıklardır; yerel etiketler DST'de tekrarlayabilir.
Kaynak zaman offset'i `original_time` alanında ve özgün dosyada kalır.

Temel ilişkiler: study -> cohort -> membership -> household;
meter -> stream ve meter -> installation -> household. Sayılar (50/100/150 vb.)
şemaya sabitlenmez. Hane isim/telefon/adres bilgisi için alan bulunmaz.

Kanonik ölçüm tekilliği `(stream_id,event_id,revision)`; provenance tekilliği
`(source_id,row_no)`; kaynak tekilliği `(sha256,contract_version,parser_version)`.
`received_at` tekrar teslimatta değişebilir; olay içeriği hash'ine dahil değildir.

Kalite kodları: `invalid_contract`, `unknown_stream`, `stream_kind_mismatch`,
`outside_stream_configuration`, `volume_out_of_range`. Sayısal normalizasyon litre
cinsindendir; 12 ondalık/18 tam basamak sınırı aşılırsa kayıt ayrılır.
Kimlik çatışması, eksik revision, çakışan dönem ve belirsiz endeks zamanı bütün
kaynak yayınını durdurur. Özgün dosya kaybolmaz; `uw retry SOURCE_ID` ile çözüm sonrası
yeniden denenir. Kısmi kaynakta registry düzeltmesi sonrası aynı komut kullanılabilir.

Özet tanıları: önceki endeks yok, negatif endeks farkı, çözümlenemeyen kurulum,
özet kovasını aşan dönem. `unallocated_liters` bilinen fakat ilgili kovaya/haneye
atanamayan hacmi gösterir. Negatif endeks farkından gerçek hacim bilinmediği için
bu toplamın içine uydurma hacim eklenmez.

Dataset Parquet sütunları: household_code, start/end (offsetli ISO-8601 UTC),
observed_liters (kayıpsız decimal string), observations, coverage_status. Gelecek
özellik/model veri setleri kendi sürümlü sözleşmelerine sahip olacaktır.

`urban-water.dataset.v2` ayrıca `normalization.parquet` içerir. Her satırda kanonik
ölçüm ID'si, stream/event/revision, özgün değer ve birim, litre, configuration_id ve
is_current bulunur. Seçili revizyonların önceki sürümleri köken zincirini korumak için
saklanır; is_current yalnız revision seçimini belirtir. Güncel hacim hesabında bunun
yanında kurulum, üyelik, kalite ve zaman kovası kuralları da uygulanır. Endeksler
ayrıca önceki referans okumasından fark alınmasını gerektirir; litre sütunu doğrudan
toplanmaz. Manifest `configurations` alanı kullanılan değişmez katsayı/dönem
sürümlerini taşır. `streams` yalnız kimlik bilgisidir; legacy katsayı ihraç edilmez.

Snapshot seçilen cohort'a ait sayaçların aday ölçümlerinden ve gerekli revision
atalarından kaynak bağımlılıklarını çıkarır. İlgisiz cohort kaynakları doğrulamaya
ve metadata'ya katılmaz. Tek kaynak paketi birden fazla hane içeriyorsa özgün paket
bütünlüğü korunur; kalite sayıları bu nedenle `referenced-source-packages` kapsamındadır.
Registry/üyelik metadata'sı seçili cohort ile sınırlıdır. Bu mantıksal kapsam filtresi,
kurumsal erişim yetkilendirmesinin yerine geçmez. Eski v1 dataset'lerin doğrulaması sürer.
