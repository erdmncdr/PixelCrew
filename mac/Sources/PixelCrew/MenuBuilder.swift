import AppKit

enum MenuBuilder {
    static func build(target: AppDelegate) -> NSMenu {
        let t = L10n.text
        let main = NSMenu()

        let app = submenu(main, "PixelCrew")
        app.addItem(withTitle: t("menu.about"), action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(item(t("menu.settings"), #selector(AppDelegate.openSettings(_:)), ",", target))
        app.addItem(item(t("menu.setup"), #selector(AppDelegate.openSetup(_:)), "", target))
        app.addItem(.separator())
        app.addItem(withTitle: t("menu.hide"), action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = app.addItem(withTitle: t("menu.hideOthers"), action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        app.addItem(withTitle: t("menu.showAll"), action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: t("menu.quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let file = submenu(main, t("menu.file"))
        file.addItem(item(t("menu.newTask"), #selector(AppDelegate.newTask(_:)), "n", target))
        let ask = item(t("menu.ask"), #selector(AppDelegate.askTeam(_:)), "n", target)
        ask.keyEquivalentModifierMask = [.command, .shift]
        file.addItem(ask)
        file.addItem(.separator())
        file.addItem(item(t("menu.workspace"), #selector(AppDelegate.chooseWorkspace(_:)), "o", target))
        file.addItem(item(t("menu.history"), #selector(AppDelegate.showHistory(_:)), "y", target))
        file.addItem(.separator())
        file.addItem(item(t("menu.stop"), #selector(AppDelegate.stopRun(_:)), ".", target))
        file.addItem(.separator())
        file.addItem(withTitle: t("menu.close"), action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        // Text fields inside the web view rely on these standard selectors.
        let edit = submenu(main, t("menu.edit"))
        edit.addItem(withTitle: t("menu.undo"), action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: t("menu.redo"), action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: t("menu.cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: t("menu.copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: t("menu.paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: t("menu.selectAll"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let view = submenu(main, t("menu.view"))
        view.addItem(item(t("menu.reload"), #selector(AppDelegate.reloadPage(_:)), "r", target))
        let full = view.addItem(withTitle: t("menu.fullScreen"), action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        full.keyEquivalentModifierMask = [.command, .control]

        let window = submenu(main, t("menu.window"))
        window.addItem(withTitle: t("menu.minimize"), action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        window.addItem(withTitle: t("menu.zoom"), action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        window.addItem(.separator())
        window.addItem(withTitle: t("menu.front"), action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        NSApp.windowsMenu = window

        let help = submenu(main, t("menu.help"))
        help.addItem(item(t("menu.log"), #selector(AppDelegate.openLog(_:)), "", target))
        help.addItem(item(t("menu.data"), #selector(AppDelegate.revealData(_:)), "", target))
        help.addItem(item(t("menu.restart"), #selector(AppDelegate.restartServer(_:)), "", target))
        NSApp.helpMenu = help

        return main
    }

    private static func submenu(_ main: NSMenu, _ title: String) -> NSMenu {
        let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let menu = NSMenu(title: title)
        holder.submenu = menu
        main.addItem(holder)
        return menu
    }

    private static func item(_ title: String, _ action: Selector, _ key: String, _ target: AnyObject) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = target
        return item
    }
}
