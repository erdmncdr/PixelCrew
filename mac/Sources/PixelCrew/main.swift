import AppKit
import Dispatch

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)

// `kill`/logout send SIGTERM; quit through AppKit so the server and its agents stop cleanly.
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { delegate.quitWithoutAsking() }
sigterm.resume()

app.run()
