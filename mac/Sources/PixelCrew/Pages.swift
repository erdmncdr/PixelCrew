import Foundation

/// Small self-contained pages shown before the server is up or when it fails.
enum Pages {
    private static let style = """
    <style>
      :root { color-scheme: dark; }
      html, body { margin: 0; height: 100%; background: #0b0b0c; color: #f1efea;
        font: 15px/1.5 -apple-system, "SF Pro Text", system-ui, sans-serif; }
      main { height: 100%; display: grid; place-content: center; justify-items: center; gap: 18px; padding: 24px; text-align: center; }
      .mark { display: flex; gap: 4px; }
      .mark i { width: 22px; height: 34px; display: block; }
      .mark i:first-child { background: #ffa552; } .mark i:last-child { background: #4fe0b6; }
      .busy .mark i { animation: hop 1.1s steps(2) infinite; }
      .busy .mark i:last-child { animation-delay: .55s; }
      @keyframes hop { 50% { transform: translateY(-6px); } }
      h1 { font-size: 20px; margin: 0; font-weight: 700; }
      p { margin: 0; color: #a9a8a3; max-width: 56ch; }
      pre { text-align: left; max-width: min(860px, 90vw); max-height: 40vh; overflow: auto; background: #141416;
        padding: 12px 14px; font: 12px/1.45 ui-monospace, Menlo, monospace; color: #c9c7c1; white-space: pre-wrap; }
      button { font: inherit; font-weight: 600; border: 0; padding: 8px 18px; cursor: pointer; background: #f1efea; color: #0b0b0c;
        border-radius: 8px; margin: 0 8px; }
      button.ghost { background: transparent; color: #a9a8a3; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.14); }
    </style>
    """

    static var loading: String {
        """
        <!doctype html><html lang="\(L10n.lang)"><meta charset="utf-8">\(style)
        <body class="busy"><main>
          <div class="mark"><i></i><i></i></div>
          <h1>\(L10n.text("page.loading"))</h1>
          <p>\(L10n.text("page.loadingSub"))</p>
        </main></body></html>
        """
    }

    static func error(message: String, log: String, offerCommandLineTools: Bool = false) -> String {
        let install = offerCommandLineTools
            ? "<button onclick=\"webkit.messageHandlers.pixelcrew.postMessage({type:'installCLT'})\">\(L10n.text("page.installCLT"))</button>"
            : ""
        let openLog = offerCommandLineTools
            ? ""
            : "<button class=\"ghost\" onclick=\"webkit.messageHandlers.pixelcrew.postMessage({type:'openLog'})\">\(L10n.text("page.openLog"))</button>"
        return """
        <!doctype html><html lang="\(L10n.lang)"><meta charset="utf-8">\(style)
        <body><main>
          <div class="mark"><i></i><i></i></div>
          <h1>\(L10n.text("page.failed"))</h1>
          <p>\(escape(message))</p>
          \(log.isEmpty ? "" : "<pre>\(escape(log))</pre>")
          <div>
            \(install)
            <button class="\(offerCommandLineTools ? "ghost" : "")" onclick="webkit.messageHandlers.pixelcrew.postMessage({type:'retry'})">\(L10n.text("page.retry"))</button>
            \(openLog)
          </div>
        </main></body></html>
        """
    }

    private static func escape(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }
}
