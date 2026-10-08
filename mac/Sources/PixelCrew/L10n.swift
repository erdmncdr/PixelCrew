import Foundation

/// Native strings (menus, alerts, the loading and error pages) in English and Turkish.
/// The language follows the app's "language" setting (auto / en / tr), where auto means
/// the system's preferred language; the page tells us when the user changes it.
enum L10n {
    static var lang: String = resolve(setting: savedSetting())

    static var systemLanguage: String {
        (Locale.preferredLanguages.first ?? "en").lowercased().hasPrefix("tr") ? "tr" : "en"
    }

    static func resolve(setting: String?) -> String {
        switch setting {
        case "en", "tr": return setting!
        default: return systemLanguage
        }
    }

    /// Reads the language from settings.json before the server is up.
    static func savedSetting() -> String? {
        let file = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/PixelCrew/settings.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return json["language"] as? String
    }

    static func text(_ key: String, _ args: CVarArg...) -> String {
        let pair = strings[key] ?? (key, key)
        let format = lang == "tr" ? pair.1 : pair.0
        return args.isEmpty ? format : String(format: format, arguments: args)
    }

    private static let strings: [String: (String, String)] = [
        // menus
        "menu.about": ("About PixelCrew", "PixelCrew Hakkında"),
        "menu.settings": ("Settings…", "Ayarlar…"),
        "menu.setup": ("Agents & Sign-in…", "Ajanlar ve Giriş…"),
        "menu.hide": ("Hide PixelCrew", "PixelCrew'u Gizle"),
        "menu.hideOthers": ("Hide Others", "Diğerlerini Gizle"),
        "menu.showAll": ("Show All", "Tümünü Göster"),
        "menu.quit": ("Quit PixelCrew", "PixelCrew'dan Çık"),
        "menu.file": ("File", "Dosya"),
        "menu.newTask": ("New Task", "Yeni İş"),
        "menu.ask": ("Ask the Crew", "Ekibe Soru Sor"),
        "menu.workspace": ("Working Folder…", "Çalışma Klasörü…"),
        "menu.history": ("History", "Geçmiş"),
        "menu.stop": ("Stop Run", "İşi Durdur"),
        "menu.close": ("Close Window", "Pencereyi Kapat"),
        "menu.edit": ("Edit", "Düzen"),
        "menu.undo": ("Undo", "Geri Al"),
        "menu.redo": ("Redo", "Yinele"),
        "menu.cut": ("Cut", "Kes"),
        "menu.copy": ("Copy", "Kopyala"),
        "menu.paste": ("Paste", "Yapıştır"),
        "menu.selectAll": ("Select All", "Tümünü Seç"),
        "menu.view": ("View", "Görünüm"),
        "menu.reload": ("Reload", "Yeniden Yükle"),
        "menu.fullScreen": ("Enter Full Screen", "Tam Ekran"),
        "menu.window": ("Window", "Pencere"),
        "menu.minimize": ("Minimize", "Küçült"),
        "menu.zoom": ("Zoom", "Büyüt"),
        "menu.front": ("Bring All to Front", "Tümünü Öne Getir"),
        "menu.help": ("Help", "Yardım"),
        "menu.log": ("Open Server Log", "Sunucu Kaydını Aç"),
        "menu.data": ("Show Data Folder", "Veri Klasörünü Göster"),
        "menu.restart": ("Restart Server", "Sunucuyu Yeniden Başlat"),
        // alerts and panels
        "quit.title": ("A run is in progress", "Çalışan bir iş var"),
        "quit.text": ("If you quit PixelCrew, the running agents stop and the run is left unfinished.",
                      "PixelCrew'u kapatırsan çalışan ajanlar durdurulur ve iş yarıda kalır."),
        "quit.confirm": ("Quit", "Kapat"),
        "quit.cancel": ("Cancel", "Vazgeç"),
        "pick.prompt": ("Choose", "Seç"),
        "pick.message": ("Choose the folder the agents will work in", "Ajanların çalışacağı klasörü seç"),
        // pages
        "page.loading": ("Opening the office…", "Ofis açılıyor…"),
        "page.loadingSub": ("Getting the crew ready.", "Ekip hazırlanıyor."),
        "page.failed": ("PixelCrew couldn't start", "PixelCrew başlatılamadı"),
        "page.retry": ("Try again", "Tekrar dene"),
        "page.openLog": ("Open the full log", "Kaydın tamamını aç"),
        "page.installCLT": ("Install Command Line Tools", "Command Line Tools'u kur"),
        // errors
        "error.clt": ("PixelCrew needs Apple's Command Line Tools (they include Python 3). Click Install Command Line Tools, finish the installer, then press Try again.",
                      "PixelCrew, Apple Command Line Tools'a ihtiyaç duyar (Python 3 bunun içinde gelir). Command Line Tools'u kur'a bas, kurulumu bitir, sonra Tekrar dene'ye bas."),
        "error.missing": ("The app bundle is incomplete: %@", "Uygulama paketi eksik: %@"),
        "error.exited": ("The server quit while starting (exit code %ld).", "Sunucu başlarken kapandı (çıkış kodu %ld)."),
        "error.timeout": ("The server wasn't ready within 40 seconds.", "Sunucu 40 saniyede hazır olmadı."),
        "error.port": ("No free port found.", "Boş port bulunamadı."),
        "error.crashed": ("The server stopped unexpectedly (exit code %ld).", "Sunucu beklenmedik şekilde kapandı (çıkış kodu %ld)."),
    ]
}
