# Claude + Codex ortak çalışma: GitHub'daki projeler

Tarih: 1 Ekim 2026. "İncelendi" işaretli projelerin README'si okundu; diğerleri arama sonuçlarındaki açıklamalarına göre listelendi.

## 1. Claude'u yönetici yapıp Codex'i çağıran eklentiler

| Proje | Ne yapıyor | Durum |
| --- | --- | --- |
| [openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc) | OpenAI'ın resmî Claude Code eklentisi. `/codex:review`, `/codex:adversarial-review` (daha şüpheci inceleme), `/codex:rescue` ile iş devri, arka plan işleri için `/codex:status`, `/codex:result`, `/codex:cancel`. Yerel `codex` CLI'ını ve app-server'ı kullanır, Node.js 18.18+ ister. | İncelendi |
| [sson0-er/claudex](https://github.com/sson0-er/claudex) | Claude Code eklentisi. Yedi aşamalı akış: gereksinim, araştırma, tasarım, planlama, uygulama, triaj, dokümantasyon. Dört insan onay kapısı var. Codex tasarım incelemesi ve uygulama için `bin/claudex-codex` başlatıcısıyla çağrılıyor; bulgular paralel inceleyicilerden (kalite, güvenlik, şartname, test) birleştiriliyor. | İncelendi |
| [AlessioZazzarini/claude-codex-collab](https://github.com/AlessioZazzarini/claude-codex-collab) | Claude proje yöneticisi, Codex ikinci mühendis. Think (en fazla 2 turluk tartışma), Build (şartname yaz, Codex arka planda uygular, Claude diff'i inceler) ve Debug (iki bağımsız hipotez) modları. 20 satırlık bash köprüsü; `OPENAI_API_KEY`'i silerek abonelikle çalışmayı zorluyor. | İncelendi |
| [DiegoHerreraDaSilva/claudex](https://github.com/DiegoHerreraDaSilva/claudex) | Claude planlıyor, Codex ve Sonnet paralel uyguluyor. | Listelendi |
| [alexzh3/codex-orchestrator](https://github.com/alexzh3/codex-orchestrator/) | Claude planlıyor ve doğruluyor, Codex uyguluyor ve bağımsız inceleme yapıyor. | Listelendi |
| [amkfbant/claude-codex-orchestration](https://github.com/amkfbant/claude-codex-orchestration) | Codex'i ayrı git worktree'lerinde sandbox içinde çalıştıran iskelet. | Listelendi |
| [junyjeon/claude-codex-orchestrator](https://github.com/junyjeon/claude-codex-orchestrator), [briandconnelly/codex-in-claude](https://github.com/briandconnelly/codex-in-claude), [jamie950315/claudex](https://github.com/jamie950315/claudex) | Codex'i Claude'dan çağırma, ikinci görüş, oturum senkronizasyonu. | Listelendi |

## 2. Çok ajanlı çalışma alanları

| Proje | Ne yapıyor |
| --- | --- |
| [qanh10x10/multiagents](https://github.com/qanh10x10/multiagents) | Claude Code, Codex ve Gemini CLI'ı MCP üzerinden haberleşen bir ekip olarak çalıştırıyor. |
| [SeemSeam/claude_codex_bridge](https://github.com/SeemSeam/claude_codex_bridge) | Birçok CLI ajanını görünür biçimde bir arada çalıştıran terminal arayüzü. |
| [xintaofei/codeg](https://github.com/xintaofei/codeg) | Farklı ajanların oturumlarını tek çalışma alanında toplayan masaüstü uygulaması. |
| [agrology/multi-review](https://github.com/agrology/multi-review) | Yazar ajan ile inceleyici ajanların sınırlı turlarla belge üzerinde uzlaştığı, insan onayıyla biten inceleme akışı. |
| [yeameen/claude-code-review-council](https://github.com/yeameen/claude-code-review-council) | Codex, Gemini ve dört uzman Claude alt ajanından paralel kod incelemesi. |

## 3. Ajanları pixel-art olarak gösterenler

| Proje | Ne yapıyor | Durum |
| --- | --- | --- |
| [pablodelucca/pixel-agents](https://github.com/pablodelucca/pixel-agents) | VS Code eklentisi. Claude Code ajanlarını ofiste çalışan karakterlere çeviriyor: dosya düzenlerken yazıyor, ararken okuyor, onay beklerken balon çıkarıyor. Olayları Claude Code hook'larından, yoksa `~/.claude/projects/` altındaki JSONL kayıtlarından alıyor. React + Canvas 2D, MIT lisanslı. | İncelendi |
| [rolandal/pixel-agents-standalone](https://github.com/rolandal/pixel-agents-standalone) | Aynı fikrin bağımsız web uygulaması. | Listelendi |
| [paulrobello/claude-office](https://github.com/paulrobello/claude-office) | Ana ajan "patron", alt ajanlar "çalışan" olarak animasyonlu ofiste. | Listelendi |
| [W17ant/Claude-Office](https://github.com/W17ant/Claude-Office) | İzometrik ofis, WebSocket olayları, Slack benzeri sohbet paneli. | Listelendi |
| [liuyixin-louis/agentroom](https://github.com/liuyixin-louis/agentroom) | Oturum arama ve kayıt tarama ile pixel-art masaüstü uygulaması. | Listelendi |

## PixelCrew'a ne aldım

- **Rol ayrımı.** Neredeyse hepsi aynı deseni kullanıyor: Claude planlıyor ve bütünleştiriyor, Codex odaklı parçaları uyguluyor ve inceliyor. PixelCrew'da planlayıcı bu güçlü yanları bilerek görev dağıtıyor; sen de onayda değiştirebiliyorsun.
- **Çapraz inceleme.** codex-plugin-cc'nin gerekçesi: farklı sağlayıcıların modelleri farklı yerlerde hata yapıyor, bu yüzden birinin işini diğerinin incelemesi, bir modelin kendini incelemesinden daha çok hata yakalıyor. PixelCrew'da her görevi diğer ajan inceliyor.
- **Net şartname.** claude-codex-collab'ın dersi: hangi dosyaların yazılacağı belirtilmezse Codex varsayım yapıyor. Planlayıcı her görev için dosya listesi ve kabul ölçütü yazmak zorunda; paralel görevlerin aynı dosyaya dokunması yasak.
- **Sınırlı tur.** 2 turdan sonra tartışma verim vermiyor (claude-codex-collab, multi-review). Düzeltme turu varsayılan 1, en fazla 3.
- **İnsan onayı.** sson0-er/claudex ve multi-review'daki onay kapısı; PixelCrew'da plan sonrası isteğe bağlı.
- **Abonelik koruması.** `OPENAI_API_KEY` ortamda kalırsa Codex sessizce API'den faturalıyor; PixelCrew bunu varsayılan olarak gizliyor.
- **Görselleştirme.** pixel-agents'ın fikri: animasyonlar süs değil, gerçek araç çağrılarından türüyor. PixelCrew'da her `Edit`, `Bash`, `file_change` olayı karakterin pozunu, duvar ekranını ve konuşma balonunu değiştiriyor.

## PixelCrew'un farkı

Yukarıdaki orkestratörler ajanlardan birinin içinde eklenti olarak çalışıyor; görselleştiriciler ise yalnızca Claude'u izliyor ve işe karışmıyor. PixelCrew ikisinin arasında tarafsız bir orkestratör: iki CLI'ı da başsız çalıştırıyor (`claude -p --output-format stream-json`, `codex exec --json`), olaylarını ortak bir dile çeviriyor, planlama, iş bölümü ve kontrol akışını kendisi yönetiyor ve hepsini aynı ofiste gösteriyor.
