import Darwin
import Foundation

/// Runs the bundled Python server (pixelcrew.py) as a child process on a free
/// loopback port, and stops it — together with any agents it started — when
/// the app quits.
final class ServerController {
    struct StartError: LocalizedError {
        let message: String
        var needsCommandLineTools = false
        var errorDescription: String? { message }
    }

    let appDir: URL
    let dataDir: URL
    let logURL: URL
    let defaultWorkspace: URL
    var onUnexpectedExit: ((Int32) -> Void)?

    private(set) var process: Process?
    private(set) var baseURL: URL?
    private var stopping = false

    init(appDir: URL) {
        let fm = FileManager.default
        let home = fm.homeDirectoryForCurrentUser
        self.appDir = appDir
        dataDir = home.appendingPathComponent("Library/Application Support/PixelCrew", isDirectory: true)
        defaultWorkspace = home.appendingPathComponent("PixelCrew/playground", isDirectory: true)
        let logs = home.appendingPathComponent("Library/Logs/PixelCrew", isDirectory: true)
        try? fm.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("server.log")
        Self.migrateLegacyData(home: home, into: dataDir)
    }

    /// The app used to be called Claudex: carry its settings, history and chats over once.
    static func migrateLegacyData(home: URL, into dataDir: URL) {
        let fm = FileManager.default
        let legacy = home.appendingPathComponent("Library/Application Support/Claudex", isDirectory: true)
        guard !fm.fileExists(atPath: dataDir.path), fm.fileExists(atPath: legacy.path) else { return }
        try? fm.copyItem(at: legacy, to: dataDir)
    }

    /// /usr/bin/python3 is only a stub until the Command Line Tools are installed;
    /// running it then pops up Apple's installer instead of starting the server.
    static func commandLineToolsInstalled() -> Bool {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        proc.arguments = ["-p"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do { try proc.run() } catch { return false }
        proc.waitUntilExit()
        return proc.terminationStatus == 0
    }

    var isRunning: Bool { process?.isRunning ?? false }

    /// Starts the server off the main thread; `completion` runs on the main thread.
    func start(completion: @escaping (Result<URL, Error>) -> Void) {
        stopping = false
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try self.launchAndWait() }
            DispatchQueue.main.async { completion(result) }
        }
    }

    private func launchAndWait() throws -> URL {
        let python = URL(fileURLWithPath: "/usr/bin/python3")
        guard Self.commandLineToolsInstalled(), FileManager.default.isExecutableFile(atPath: python.path) else {
            throw StartError(message: L10n.text("error.clt"), needsCommandLineTools: true)
        }
        let script = appDir.appendingPathComponent("pixelcrew.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw StartError(message: L10n.text("error.missing", script.path))
        }
        // A stable port keeps the page origin (and its saved preferences) the same across launches.
        let port: Int
        if let stable = try? Self.freePort(preferred: 8789) {
            port = stable
        } else {
            port = try Self.freePort(preferred: 0)
        }
        let log = try openLog()

        let proc = Process()
        proc.executableURL = python
        proc.arguments = [script.path, "--no-browser", "--port", String(port)]
        proc.currentDirectoryURL = appDir
        proc.environment = environment()
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = log
        proc.standardError = log
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self, !self.stopping else { return }
                self.onUnexpectedExit?(p.terminationStatus)
            }
        }
        try proc.run()
        process = proc

        let url = URL(string: "http://127.0.0.1:\(port)/")!
        let deadline = Date().addingTimeInterval(40)
        while Date() < deadline {
            if !proc.isRunning {
                throw StartError(message: L10n.text("error.exited", Int(proc.terminationStatus)))
            }
            if Self.ping(url.appendingPathComponent("api/state")) {
                baseURL = url
                return url
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        throw StartError(message: L10n.text("error.timeout"))
    }

    /// SIGINT first: the server's handler cancels running agents before exiting.
    func stop() {
        stopping = true
        guard let proc = process, proc.isRunning else { return }
        proc.interrupt()
        let deadline = Date().addingTimeInterval(6)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if proc.isRunning {
            proc.terminate()
            Thread.sleep(forTimeInterval: 0.5)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
    }

    /// True when the server reports a task run that is still going.
    func hasActiveRun() -> Bool {
        guard let base = baseURL,
              let data = Self.get(base.appendingPathComponent("api/state"), timeout: 1.5),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let run = json["run"] as? [String: Any] else { return false }
        return (run["status"] as? String) == "running" || (json["chatBusy"] as? Bool) == true
    }

    func logTail(lines: Int = 40) -> String {
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return "" }
        return text.split(separator: "\n", omittingEmptySubsequences: false).suffix(lines).joined(separator: "\n")
    }

    // MARK: - helpers

    private func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        // Apps opened from Finder get a minimal PATH; agents need the user's tools (brew, node, …).
        if let path = Self.loginShellPath() { env["PATH"] = path }
        env["PIXELCREW_DATA_DIR"] = dataDir.path
        env["PIXELCREW_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["PIXELCREW_DEFAULT_WORKSPACE"] = defaultWorkspace.path
        env["PIXELCREW_SYSTEM_LANGUAGE"] = L10n.systemLanguage
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // never write .pyc into the signed bundle
        env["PYTHONIOENCODING"] = "utf-8"
        if env["LANG"] == nil { env["LANG"] = "en_US.UTF-8" }
        return env
    }

    private func openLog() throws -> FileHandle {
        let fm = FileManager.default
        if let size = (try? fm.attributesOfItem(atPath: logURL.path))?[.size] as? Int, size > 2_000_000 {
            try? fm.removeItem(at: logURL)
        }
        if !fm.fileExists(atPath: logURL.path) {
            fm.createFile(atPath: logURL.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: logURL)
        handle.seekToEndOfFile()
        let stamp = ISO8601DateFormatter().string(from: Date())
        handle.write("\n===== PixelCrew started \(stamp) =====\n".data(using: .utf8)!)
        return handle
    }

    static func loginShellPath() -> String? {
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: shell)
        proc.arguments = ["-lic", "printf '__PATH__%s' \"$PATH\""]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        proc.standardInput = FileHandle.nullDevice
        do { try proc.run() } catch { return nil }
        let deadline = Date().addingTimeInterval(6)
        while proc.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if proc.isRunning { proc.terminate(); return nil }
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        guard let range = out.range(of: "__PATH__", options: .backwards) else { return nil }
        let path = out[range.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
        return path.isEmpty ? nil : path
    }

    static func freePort(preferred: UInt16) throws -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw StartError(message: L10n.text("error.port")) }
        defer { close(fd) }
        // Match the server's own SO_REUSEADDR so a port left in TIME_WAIT by the last run still counts as free.
        var one: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = preferred.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, len) }
        }
        guard bound == 0 else { throw StartError(message: L10n.text("error.port")) }
        let named = withUnsafeMutablePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) }
        }
        guard named == 0 else { throw StartError(message: L10n.text("error.port")) }
        return Int(UInt16(bigEndian: addr.sin_port))
    }

    static func ping(_ url: URL) -> Bool {
        get(url, timeout: 1) != nil
    }

    static func get(_ url: URL, timeout: TimeInterval) -> Data? {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.httpMethod = "GET"
        let semaphore = DispatchSemaphore(value: 0)
        var result: Data?
        URLSession.shared.dataTask(with: request) { data, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200 { result = data }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + timeout + 0.5)
        return result
    }
}
