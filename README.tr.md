# PixelCrew

[English](README.md) · **Türkçe**

PixelCrew, zaten kullandığın yapay zekâ kodlama araçlarını (**Claude Code, Codex, Gemini (Antigravity CLI) ve Grok Build**) tek bir ekip gibi çalıştıran bir macOS uygulaması. Bir iş yazıyorsun; ajanlar işi planlıyor, aralarında bölüşüyor, aynı anda çalışıyor ve birbirlerinin işini kontrol ediyor. Hepsini pixel-art bir ofiste canlı izliyorsun: kim hangi dosyayı yazıyor, hangi komutu çalıştırıyor, kimin işi kontrolde.

PixelCrew kendi modelini getirmez. Mac'indeki resmi CLI'ları, senin aboneliklerin ya da API anahtarlarınla çalıştırır.

## Kurulum

1. [Son sürümden](../../releases/latest) `PixelCrew-<sürüm>.dmg` dosyasını indir.
2. Aç ve **PixelCrew**'u **Uygulamalar** klasörüne sürükle.
3. PixelCrew'u aç. İlk açılışta **Ekibini kur** ekranı gelir.

Gerekenler: macOS 14 veya üstü (Apple silicon ya da Intel) ve Python 3'ü getiren Apple Command Line Tools. Eksikse PixelCrew kurmayı önerir.

## Ajanları bağla

Kurulum ekranında her ajan için bir kart var. En az birini bağla; her biri kendi aboneliğini ya da bir API anahtarını kullanabilir.

| Ajan | Çalıştırdığı araç | Abonelikle giriş | API anahtarı |
| --- | --- | --- | --- |
| Claude | Claude Code | Claude Pro / Max (`claude auth login`) | Anthropic Console anahtarı |
| Codex | Codex CLI | ChatGPT aboneliği (`codex login`) | OpenAI platform anahtarı |
| Gemini | Antigravity CLI (`agy`) | Google hesabı (`agy`) | Google AI Studio anahtarı |
| Grok | Grok Build | SuperGrok / X Premium+ (`grok login`) | xAI Console anahtarı |

- **Terminal'de kur**, aracın resmi kurulum komutuyla Terminal'i açar. Antigravity CLI için kurulum kılavuzunun bağlantısı verilir.
- **Terminal'de giriş yap**, aracın giriş komutuyla Terminal'i açar; girişi orada tamamla, kart kendiliğinden yeşile döner.
- **API anahtarı**, anahtarı macOS Anahtar Zinciri'nde saklar (PixelCrew dosyalarına yazılmaz) ve yalnızca o ajanın CLI'ına verir. API kullanımı sağlayıcı hesabına faturalanır. CLI'ın yine de kurulu olması gerekir.
- **Ekipte**, işe kimin katılacağını seçer. İş hazır ajanlar arasında paylaşılır; hiçbiri hazır değilse **Demo**'yu açıp hiçbir şey harcamayan simülasyonu izleyebilirsin.

Bu ekrana **PixelCrew > Ajanlar ve Giriş…** menüsünden ya da **Ayarlar > Giriş ve API anahtarları…** düğmesinden dönebilirsin.

## İki mod

- **İş yaptır:** İsteğin planlanır, ajanlara bölünür, yapılır ve kontrol edilir. "merhaba" gibi iş olmayan bir şey yazarsan plan yapılmadan doğrudan cevap verilir.
- **Soru sor:** Sorun bir ajana ya da bütün ekibe gider. Klasördeki dosyaları okuyup cevap verirler, hiçbir şeyi değiştirmezler. Takip soruları aynı konuşmanın devamıdır.

## Bir iş nasıl ilerler

1. **Planlama.** Kıdemli bir ajan çalışma klasörüne bakar ve isteği 1-6 göreve böler; her görevin sahibi, dokunacağı dosyalar ve kabul ölçütü vardır.
2. **Plan kontrolü.** Başka bir ajan planı eleştirir; eksik adım, yanlış sıra ya da dosya çakışması görürse düzeltir.
3. **Onay.** "Planı onayla" açıksa ekip seni bekler. Görev kartındaki ajan adına tıklayarak işi başkasına verebilirsin.
4. **Uygulama.** Bağımlılığı biten görevler başlar; ajanlar aynı anda farklı görevlerde çalışır.
5. **Çapraz kontrol.** Biten her görevi başka bir ajan inceler; dosya okuyup test çalıştırabilir ama düzenleyemez. Gerçek bir sorun görev sahibine düzeltme için döner.
6. **Son kontrol.** Birden çok görevli işlerde planlayıcı testleri çalıştırır, parçalar uyuşmuyorsa en küçük düzeltmeyi yapar.
7. **Özet.** Süre, görev sonuçları, değişen dosyalar ve token kullanımı.

### Roller ve modeller

Her ajan **kıdemli** ya da **işçi**dir (Ayarlar > Ekip; varsayılan olarak Claude ve Codex kıdemli). Kıdemliler planı yapar, zor ve modüller arası görevleri alır, kontrol eder; işçiler net tanımlı, rutin görevleri alır. Her iş zorluğuna göre güçlü, standart ya da hızlı modelle çalışır:

| Kademe | İşler | Claude | Codex | Gemini | Grok |
| --- | --- | --- | --- | --- | --- |
| Güçlü | plan, plan kontrolü, son kontrol, zor görevler | Opus | GPT-6 Astra | Gemini 3.1 Pro (yüksek) | Grok 4.7 |
| Standart | orta görevler, sohbet | Sonnet | GPT-6.1 Sol | Gemini 3.1 Pro (yüksek) | Grok 4.7 |
| Hızlı | kolay görevler, düzeltme sonrası kontrol | Haiku | GPT-6 Luna | Gemini 3.1 Pro (düşük) | Grok 4.7, düşük efor |

Ayarlardan bir ajana sabit model verebilir ya da her CLI'ın varsayılanına bırakabilirsin.

### Limit dengeleme

PixelCrew her ajanın kalan kullanım limitini okur (Claude ve Gemini CLI'ların kendi `/usage` komutuyla, Codex oturum kayıtlarından, Grok kendi `/usage` ekranının kullandığı faturalama çağrısıyla). Limiti azalan ajan bir model kademesi iner ve ağır görev almaz; limiti bitmek üzere olan ajan görevlerini, kontrollerini ve planlayıcılığı diğerlerine bırakır; işin ortasında limite takılan işi başka bir ajan devralır. Her karar gerekçesiyle Görevler sekmesinde yazar.

## Yetki ve güvenlik

- **Güvenli (varsayılan):** Ajanlar çalışma klasöründeki dosyaları düzenleyebilir. Claude yalnızca bilinen geliştirici komutlarını çalıştırır; Codex, Grok ve Gemini klasör dışına yazamayan bir sandbox'ta çalışır. Planlama, soru ve kontrol işleri salt okunurdur.
- **Tam yetki:** Her komut onay sormadan çalışır, internet erişimi açıktır. Yalnızca güvendiğin klasörlerde kullan.

Güvenli modda da ajanlar dosya değiştirir ve `npm`, `python` gibi kod çalıştırabilen komutlar kullanır. Önemli bir projede çalışmadan önce işini git ile kaydet.

Yerel sunucu yalnızca `127.0.0.1`'i dinler, her istekte `Host` başlığını kontrol eder ve durum değiştiren istekleri yalnızca aynı kaynaktan JSON olarak kabul eder. Ajanlar yalnızca seçtiğin klasörde başlar; ev klasörünün tamamı ya da `/` seçilemez.

## Dil

İngilizce ve Türkçe. PixelCrew sistem dilini izler; Ayarlar'dan ya da kurulum ekranından değiştirebilirsin.

## Güncellemeler

PixelCrew açılışta ve günde bir kez bu deponun son sürümünü denetler. Yeni sürüm çıktığında bir şerit güncellemeyi önerir: yeni DMG indirilir, içindeki uygulama yalnızca aynı Developer ID ekibince imzalanmış ve Apple onaylıysa kurulur, PixelCrew yeniden başlarken eskisinin yerine geçer (eski kopya Çöp Sepeti'ne gider). Otomatik denetlemeyi Ayarlar'dan kapatabilir, istediğin zaman **PixelCrew > Güncellemeleri Denetle…** menüsünü kullanabilirsin.

## Veriler

Ayarlar, geçmiş ve sohbetler `~/Library/Application Support/PixelCrew` içinde, sunucu kaydı `~/Library/Logs/PixelCrew/server.log` içindedir. Yardım menüsü ikisini de açar. API anahtarları giriş anahtar zincirinde `app.pixelcrew.PixelCrew` adıyla durur. Uygulamanın eski adı Claudex'ti; ilk açılışta eski veriler kendiliğinden taşınır.

## Kaynaktan derleme

```bash
git clone <bu depo>
cd PixelCrew
./script/build_and_run.sh            # dist/ içine derler ve açar
./script/build_and_run.sh --install  # /Applications içine kurar
```

Sunucu düz Python 3'tür (yalnızca standart kütüphane); geliştirirken tarayıcıda da çalışır:

```bash
python3 pixelcrew.py                 # http://127.0.0.1:8787, veriler ./data içinde
node --test tests/office-routines.test.cjs
```

### Sürüm çıkarma

```bash
xcrun notarytool store-credentials pixelcrew   # bir kez (App Store Connect API anahtarı ya da uygulamaya özel parola)
gh auth login                                  # bir kez, GitHub CLI ile
NOTARY_PROFILE=pixelcrew PUBLISH=1 ./script/release.sh
```

`release.sh` evrensel uygulamayı derler, Developer ID ve hardened runtime ile imzalar, uygulamayı ve DMG'yi notarize edip zımbalar, `dist/PixelCrew-<sürüm>.dmg` dosyasını SHA-256 özetiyle birlikte üretir. `PUBLISH=1` ile ayrıca commit'i etiketler, GitHub sürümünü DMG ve `release-notes/<sürüm>.md` notlarıyla yayınlar ve yayınlanan indirmenin eşleştiğini kontrol eder. Sürüm numarası `VERSION` dosyasından gelir; kurulu kopyalar yeni sürümü uygulama içi güncelleyiciyle alır.

## Sorun giderme

- **Giriş yaptığım hâlde kart "Giriş gerekli" diyor.** **Tekrar kontrol et**'e bas. Claude masaüstü uygulamasının oturumu CLI'a geçmez; girişi karttaki düğmeyle yap.
- **Araç kurulu ama bulunamıyor.** PixelCrew `~/.local/bin`, Homebrew ve giriş kabuğunun `PATH`'ine bakar. Başka bir yeri `PIXELCREW_CLAUDE_BIN`, `PIXELCREW_CODEX_BIN`, `PIXELCREW_AGY_BIN`, `PIXELCREW_GEMINI_BIN` ya da `PIXELCREW_GROK_BIN` ile göster.
- **Bir ajan takıldı.** **Durdur** bütün ajan süreçlerini sonlandırır. Her işin bir süre sınırı vardır (varsayılan 30 dakika, Ayarlar'dan değişir).

Projenin arkasındaki araştırma notları: [docs/ARASTIRMA.md](docs/ARASTIRMA.md)

## Lisans

[Apache License 2.0](LICENSE). Claude, Codex, Gemini ve Grok sahiplerinin ticari markalarıdır; PixelCrew'un Anthropic, OpenAI, Google ya da xAI ile bir bağı yoktur.
