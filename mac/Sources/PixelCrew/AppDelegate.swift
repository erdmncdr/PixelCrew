import AppKit
import UserNotifications
import WebKit

/// Weak trampoline so the WKUserContentController does not retain the delegate.
private final class ScriptProxy: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(c, didReceive: message)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
    WKScriptMessageHandler, UNUserNotificationCenterDelegate {

    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: ServerController!
    private var confirmedQuit = false

    static let ink = NSColor(srgbRed: 0x0B / 255, green: 0x0B / 255, blue: 0x0C / 255, alpha: 1)

    // MARK: - lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        let appDir = Bundle.main.resourceURL!.appendingPathComponent("app", isDirectory: true)
        server = ServerController(appDir: appDir)
        server.onUnexpectedExit = { [weak self] code in
            guard let self else { return }
            self.webView.loadHTMLString(Pages.error(
                message: L10n.text("error.crashed", Int(code)),
                log: self.server.logTail()), baseURL: nil)
        }

        NSApp.mainMenu = MenuBuilder.build(target: self)
        buildWindow()
        webView.loadHTMLString(Pages.loading, baseURL: nil)

        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }

        startServer()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if confirmedQuit || !server.hasActiveRun() { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = L10n.text("quit.title")
        alert.informativeText = L10n.text("quit.text")
        alert.addButton(withTitle: L10n.text("quit.confirm"))
        alert.addButton(withTitle: L10n.text("quit.cancel"))
        alert.alertStyle = .warning
        let answer = alert.runModal()
        confirmedQuit = answer == .alertFirstButtonReturn
        return confirmedQuit ? .terminateNow : .terminateCancel
    }

    func quitWithoutAsking() {
        confirmedQuit = true
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        server.stop()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        NSApp.dockTile.badgeLabel = nil
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Closing the only window quits; route it through the same confirmation.
        NSApp.terminate(nil)
        return false
    }

    // MARK: - window

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.userContentController.add(ScriptProxy(self), name: "pixelcrew")
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.underPageBackgroundColor = Self.ink
        webView.allowsMagnification = true

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "PixelCrew"
        window.titleVisibility = .hidden  // the page shows the brand and folder itself
        window.titlebarSeparatorStyle = .none
        window.titlebarAppearsTransparent = true
        window.backgroundColor = Self.ink
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 980, height: 660)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        if !window.setFrameUsingName("PixelCrewMain") { window.center() }
        window.setFrameAutosaveName("PixelCrewMain")
        window.makeKeyAndOrderFront(nil)
    }

    private func startServer() {
        webView.loadHTMLString(Pages.loading, baseURL: nil)
        server.start { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let url):
                self.webView.load(URLRequest(url: url))
            case .failure(let error):
                let clt = (error as? ServerController.StartError)?.needsCommandLineTools ?? false
                self.webView.loadHTMLString(
                    Pages.error(message: error.localizedDescription, log: clt ? "" : self.server.logTail(),
                                offerCommandLineTools: clt), baseURL: nil)
            }
        }
    }

    // MARK: - web view

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // The app only ever shows its own local server; anything else opens in the browser.
        guard let url = action.request.url else { return decisionHandler(.allow) }
        if ["about", "data"].contains(url.scheme ?? "") || url.host == "127.0.0.1" {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let url = server.baseURL { webView.load(URLRequest(url: url)) }
    }

    // MARK: - bridge from the page (window.webkit.messageHandlers.pixelcrew)

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "pickFolder":
            pickFolder(id: body["id"] as? String ?? "", start: body["start"] as? String)
        case "notify":
            notify(kind: body["kind"] as? String ?? "", title: body["title"] as? String ?? "PixelCrew",
                   text: body["body"] as? String ?? "")
        case "title":
            window.subtitle = body["subtitle"] as? String ?? ""
        case "retry":
            server.stop()
            startServer()
        case "openLog":
            openLog(nil)
        case "installCLT":
            installCommandLineTools()
        case "language":
            L10n.lang = L10n.resolve(setting: body["lang"] as? String)
            NSApp.mainMenu = MenuBuilder.build(target: self)
        default:
            break
        }
    }

    private func reply(_ id: String, _ value: Any?) {
        let arg: String
        if let value, let data = try? JSONSerialization.data(withJSONObject: value, options: .fragmentsAllowed),
           let text = String(data: data, encoding: .utf8) {
            arg = text
        } else {
            arg = "null"
        }
        let idJSON = (try? JSONSerialization.data(withJSONObject: id, options: .fragmentsAllowed))
            .flatMap { String(data: $0, encoding: .utf8) } ?? "\"\""
        webView.evaluateJavaScript("window.PixelCrewNative && window.PixelCrewNative.reply(\(idJSON), \(arg))")
    }

    private func pickFolder(id: String, start: String?) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = L10n.text("pick.prompt")
        panel.message = L10n.text("pick.message")
        if let start, FileManager.default.fileExists(atPath: start) {
            panel.directoryURL = URL(fileURLWithPath: start)
        }
        panel.beginSheetModal(for: window) { [weak self] response in
            self?.reply(id, response == .OK ? panel.url?.path : nil)
        }
    }

    private func notify(kind: String, title: String, text: String) {
        // Only interrupt the user when they are looking elsewhere.
        if NSApp.isActive && window.isKeyWindow { return }
        NSApp.dockTile.badgeLabel = kind == "approval" ? "!" : "●"
        NSApp.requestUserAttention(kind == "approval" ? .criticalRequest : .informationalRequest)
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = text
        content.sound = .default
        let request = UNNotificationRequest(identifier: "pixelcrew-\(kind)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        completionHandler()
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    // MARK: - menu actions

    private func page(_ action: String) {
        webView.evaluateJavaScript("window.PixelCrewNative && window.PixelCrewNative.action('\(action)')")
    }

    @objc func newTask(_ sender: Any?) { page("newTask") }
    @objc func askTeam(_ sender: Any?) { page("ask") }
    @objc func openSettings(_ sender: Any?) { page("settings") }
    @objc func openSetup(_ sender: Any?) { page("setup") }
    @objc func chooseWorkspace(_ sender: Any?) { page("workspace") }
    @objc func showHistory(_ sender: Any?) { page("history") }
    @objc func stopRun(_ sender: Any?) { page("stop") }
    @objc func reloadPage(_ sender: Any?) {
        if let url = server.baseURL, server.isRunning { webView.load(URLRequest(url: url)) } else { startServer() }
    }
    @objc func openLog(_ sender: Any?) { NSWorkspace.shared.open(server.logURL) }
    @objc func revealData(_ sender: Any?) { NSWorkspace.shared.activateFileViewerSelecting([server.dataDir]) }
    @objc func restartServer(_ sender: Any?) {
        server.stop()
        startServer()
    }

    /// Apple's own installer for the Command Line Tools (which bring /usr/bin/python3).
    private func installCommandLineTools() {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        proc.arguments = ["--install"]
        try? proc.run()
    }
}
