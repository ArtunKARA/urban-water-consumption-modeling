# Doğrulama kapsamı

Bu pakette otomatik kabul testleri sistem davranışını kontrol eder. Bunlar bir
formal model checker çalıştırılmış olduğu veya tüm ML çıktılarının kanıtlandığı
iddiası taşımaz.

| Özellik | İlk pakette kanıt |
|---|---|
| Aynı olay yeniden gelince hacim artmaz | overlap/redelivery testi |
| Farklı iki olay aynı anda gelebilir | aynı timestamp ve değerde iki pulse testi |
| Staging görünmez | crash-resume testi, yayın öncesi toplam sıfır |
| Eski worker yazamaz | süresi dolmuş token ile LostLease testi |
| Düzeltme eski dataset'i değiştirmez | freeze -> revision -> verify testi |
| Negatif endeks farkı sıfır tüketim olmaz | reset tanısı ve bağımsız hacim hesabı |
| Dönem toplamından saatlik şekil uydurulmaz | cross-bucket interval testi |
| Yedek geri yüklenebilir | yeni DB/dizin, sayaçlar + sonuçlar + manifest doğrulaması |

Anomali makalesinin formal aşamasında ayrıca sonlu durum modeli, ölçümden sembole
soyutlama, kod/model iz uyumu, erişilebilirlik, deadlock/vacuity kontrolleri ve
karşı örnekler eklenecek. Şebeke ve insan davranışı varsayımları açık yazılacak;
checker sonucu model dışındaki bütün fiziksel kaçakları doğrulamış sayılmayacak.
