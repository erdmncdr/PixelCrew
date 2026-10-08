import AppKit
import Security

/// Self-update from GitHub Releases. The latest release is checked on launch and once a day;
/// an update is installed only when the app inside its DMG is signed by the same Developer ID
/// team as this build and accepted by Gatekeeper (notarized). The new bundle is staged next to
/// the running one and swapped in by a small helper after PixelCrew quits, then relaunched;
/// the old bundle goes to the Trash.
///
/// The feed and the team come from Info.plist (PixelCrewUpdateFeed, PixelCrewTeamID), which the
/// build script fills in for Developer ID builds; ad-hoc development builds don't self-update.
/// PIXELCREW_UPDATE_TEST=1 installs whatever update the first check finds and restarts, so the
/// whole path can be tried end to end without clicking.
final class Updater {
    struct Release {
        let version: String
        let page: URL
        let dmg: URL
        let size: Int
    }

    enum State {
        case idle
        case checking
        case upToDate
        case available(Release)
        case downloading(Release)
        case verifying(Release)
        case ready(Release)
        case manual(Release)          // couldn't swap in place; the DMG is open for a drag
        case failed(String)
    }

    var onChange: ((State, Bool) -> Void)?   // state, and whether the user asked for it
    private(set) var state: State = .idle

    private let feed: URL?
    private let teamID: String?
    private var userAsked = false
    private var timer: Timer?
    private var staged: URL?
    private let skipKey = "PixelCrewSkippedVersion"

    var current: String { Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0" }
    var enabled: Bool { feed != nil && teamID != nil }
    var pendingInstall: Bool { staged != nil }

    init() {
        let info = Bundle.main.infoDictionary ?? [:]
        feed = (info["PixelCrewUpdateFeed"] as? String).flatMap(URL.init(string:))
        let team = (info["PixelCrewTeamID"] as? String) ?? ""
        teamID = team.isEmpty ? nil : team
    }

    /// Automatic checks: shortly after launch, then every 24 hours, unless turned off in Settings.
    func startSchedule() {
        guard enabled else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in self?.autoCheck() }
        timer = Timer.scheduledTimer(withTimeInterval: 24 * 3600, repeats: true) { [weak self] _ in self?.autoCheck() }
    }

    private func autoCheck() {
        guard Self.autoUpdateSetting() else { return }
        check(userInitiated: false)
    }

    func check(userInitiated: Bool) {
        userAsked = userInitiated
        guard let feed, enabled else {
            if userInitiated { set(.failed(L10n.text("update.devBuild"))) }
            return
        }
        switch state {
        case .checking, .downloading, .verifying, .ready: return
        default: break
        }
        set(.checking)
        var request = URLRequest(url: feed, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("PixelCrew/\(current)", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async { self?.handleFeed(data: data, response: response, error: error) }
        }.resume()
    }

    private func handleFeed(data: Data?, response: URLResponse?, error: Error?) {
        guard let data, (response as? HTTPURLResponse)?.statusCode == 200,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tag = json["tag_name"] as? String,
              let page = (json["html_url"] as? String).flatMap(URL.init(string:)) else {
            set(userAsked ? .failed(error?.localizedDescription ?? L10n.text("update.feedFailed")) : .idle)
            return
        }
        let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
        let assets = json["assets"] as? [[String: Any]] ?? []
        guard Self.isNewer(version, than: current) else { set(userAsked ? .upToDate : .idle); return }
        guard let asset = assets.first(where: { ($0["name"] as? String)?.hasSuffix(".dmg") == true }),
              let url = (asset["browser_download_url"] as? String).flatMap(URL.init(string:)) else {
            set(userAsked ? .failed(L10n.text("update.noAsset")) : .idle)
            return
        }
        if !userAsked && UserDefaults.standard.string(forKey: skipKey) == version { set(.idle); return }
        set(.available(Release(version: version, page: page, dmg: url, size: asset["size"] as? Int ?? 0)))
    }

    func skip() {
        if case .available(let release) = state { UserDefaults.standard.set(release.version, forKey: skipKey) }
        set(.idle)
    }

    func dismiss() {
        if case .ready = state { return }   // the staged update still installs on the next quit
        set(.idle)
    }

    /// Download, verify and stage the release.
    func install() {
        guard case .available(let release) = state else { return }
        set(.downloading(release))
        let work = FileManager.default.temporaryDirectory.appendingPathComponent("PixelCrew-update-\(UUID().uuidString)", isDirectory: true)
        URLSession.shared.downloadTask(with: release.dmg) { [weak self] location, response, error in
            guard let self else { return }
            guard let location, (response as? HTTPURLResponse)?.statusCode == 200 else {
                DispatchQueue.main.async { self.set(.failed(error?.localizedDescription ?? L10n.text("update.downloadFailed"))) }
                return
            }
            do {
                try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
                let dmg = work.appendingPathComponent("PixelCrew.dmg")
                try FileManager.default.moveItem(at: location, to: dmg)
                DispatchQueue.main.async { self.set(.verifying(release)) }
                let result = try self.stage(dmg: dmg, work: work, release: release)
                try? FileManager.default.removeItem(at: work)
                DispatchQueue.main.async {
                    switch result {
                    case .staged(let url): self.staged = url; self.set(.ready(release))
                    case .manual: self.set(.manual(release))
                    }
                }
            } catch {
                try? FileManager.default.removeItem(at: work)
                DispatchQueue.main.async { self.set(.failed(error.localizedDescription)) }
            }
        }.resume()
    }

    struct UpdateError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private enum StageResult { case staged(URL), manual }

    /// Mounts the DMG, checks the app inside and copies it next to the running bundle.
    private func stage(dmg: URL, work: URL, release: Release) throws -> StageResult {
        let mount = work.appendingPathComponent("mount", isDirectory: true)
        try FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true)
        guard Self.run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount.path, dmg.path]) == 0 else {
            throw UpdateError(message: L10n.text("update.mountFailed"))
        }
        defer { _ = Self.run("/usr/bin/hdiutil", ["detach", mount.path, "-force"]) }
        let app = mount.appendingPathComponent("PixelCrew.app")
        try verify(app: app, version: release.version)

        let target = Bundle.main.bundleURL
        let parent = target.deletingLastPathComponent()
        guard FileManager.default.isWritableFile(atPath: parent.path), !target.path.contains("/AppTranslocation/") else {
            // e.g. running from a read-only place: let the user drag the new copy into Applications
            let keep = FileManager.default.temporaryDirectory.appendingPathComponent("PixelCrew-\(release.version).dmg")
            try? FileManager.default.removeItem(at: keep)
            try FileManager.default.copyItem(at: dmg, to: keep)
            NSWorkspace.shared.open(keep)
            return .manual
        }
        let stageDir = parent.appendingPathComponent(".PixelCrew-update-\(release.version)", isDirectory: true)
        try? FileManager.default.removeItem(at: stageDir)
        try FileManager.default.createDirectory(at: stageDir, withIntermediateDirectories: true)
        let staged = stageDir.appendingPathComponent(target.lastPathComponent)
        guard Self.run("/usr/bin/ditto", [app.path, staged.path]) == 0 else {
            throw UpdateError(message: L10n.text("update.copyFailed"))
        }
        try verify(app: staged, version: release.version)   // the copy, not just the original
        return .staged(staged)
    }

    /// Same team's Developer ID signature, valid on every architecture, accepted by Gatekeeper,
    /// and the expected bundle id and version.
    private func verify(app: URL, version: String) throws {
        guard let teamID else { throw UpdateError(message: L10n.text("update.devBuild")) }
        var code: SecStaticCode?
        var requirement: SecRequirement?
        let text = "anchor apple generic and certificate leaf[subject.OU] = \"\(teamID)\" and identifier \"\(Bundle.main.bundleIdentifier ?? "")\""
        guard SecStaticCodeCreateWithPath(app as CFURL, [], &code) == errSecSuccess, let code,
              SecRequirementCreateWithString(text as CFString, [], &requirement) == errSecSuccess, let requirement,
              SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate | kSecCSCheckNestedCode),
                                         requirement) == errSecSuccess else {
            throw UpdateError(message: L10n.text("update.badSignature"))
        }
        guard Self.run("/usr/sbin/spctl", ["--assess", "--type", "execute", app.path]) == 0 else {
            throw UpdateError(message: L10n.text("update.notNotarized"))
        }
        let info = NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist")) as? [String: Any] ?? [:]
        guard info["CFBundleIdentifier"] as? String == Bundle.main.bundleIdentifier,
              info["CFBundleShortVersionString"] as? String == version else {
            throw UpdateError(message: L10n.text("update.badBundle"))
        }
    }

    /// Called from applicationWillTerminate: hand the staged bundle to a helper that swaps it
    /// in once this process is gone and opens the new version.
    func launchSwapHelper() {
        guard let staged else { return }
        let target = Bundle.main.bundleURL
        let trash = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".Trash", isDirectory: true)
        var backup = trash.appendingPathComponent("PixelCrew \(current).app")
        if FileManager.default.fileExists(atPath: backup.path) {
            backup = trash.appendingPathComponent("PixelCrew \(current) \(Int(Date().timeIntervalSince1970)).app")
        }
        let script = """
        pid="$1"; target="$2"; staged="$3"; backup="$4"
        i=0
        while kill -0 "$pid" 2>/dev/null; do i=$((i+1)); [ "$i" -gt 300 ] && exit 1; sleep 0.2; done
        if mv "$target" "$backup"; then
          if mv "$staged" "$target"; then rmdir "$(dirname "$staged")" 2>/dev/null; else mv "$backup" "$target"; fi
        fi
        open "$target"
        """
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/sh")
        proc.arguments = ["-c", script, "pixelcrew-update", String(ProcessInfo.processInfo.processIdentifier),
                          target.path, staged.path, backup.path]
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
    }

    // MARK: - helpers

    private let testMode = ProcessInfo.processInfo.environment["PIXELCREW_UPDATE_TEST"] == "1"

    private func set(_ new: State) {
        state = new
        onChange?(new, userAsked)
        guard testMode else { return }
        if case .available = new { DispatchQueue.main.async { self.install() } }
        if case .ready = new { DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApp.terminate(nil) } }
    }

    static func isNewer(_ a: String, than b: String) -> Bool {
        func parts(_ v: String) -> [Int] {
            v.split(separator: "-").first.map { $0.split(separator: ".").map { Int($0) ?? 0 } } ?? []
        }
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let l = i < x.count ? x[i] : 0, r = i < y.count ? y[i] : 0
            if l != r { return l > r }
        }
        return false
    }

    /// The "autoUpdate" setting, read straight from settings.json (default on).
    static func autoUpdateSetting() -> Bool {
        let file = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/PixelCrew/settings.json")
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return true }
        return json["autoUpdate"] as? Bool ?? true
    }

    @discardableResult
    static func run(_ tool: String, _ args: [String]) -> Int32 {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: tool)
        proc.arguments = args
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do { try proc.run() } catch { return -1 }
        proc.waitUntilExit()
        return proc.terminationStatus
    }

    /// The state as JSON for the page's update bar.
    func payload(_ state: State, userAsked: Bool) -> [String: Any] {
        var out: [String: Any] = ["current": current, "userAsked": userAsked]
        func add(_ kind: String, _ release: Release?) {
            out["state"] = kind
            if let release { out["version"] = release.version; out["page"] = release.page.absoluteString; out["size"] = release.size }
        }
        switch state {
        case .idle: add("idle", nil)
        case .checking: add("checking", nil)
        case .upToDate: add("upToDate", nil)
        case .available(let r): add("available", r)
        case .downloading(let r): add("downloading", r)
        case .verifying(let r): add("verifying", r)
        case .ready(let r): add("ready", r)
        case .manual(let r): add("manual", r)
        case .failed(let message): add("failed", nil); out["error"] = message
        }
        return out
    }
}
