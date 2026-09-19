import SwiftUI
import AppKit
import AVKit
import UniformTypeIdentifiers

struct Subtitle: Codable, Identifiable {
    var id: String { url }
    var url: String
    var label: String
    var language: String?
}
struct Handoff: Codable {
    var url: String
    var title: String?
    var referer: String?
    var currentTime: Double?
    var subtitles: [Subtitle]?
}

@MainActor final class Playback: ObservableObject {
    static let shared = Playback()
    let player = AVPlayer()
    @Published var source = ""
    @Published var title = "VideoBridge"
    @Published var message = "Open a video or send one from your browser."
    @Published var warning = ""
    @Published var subtitles: [Subtitle] = []
    @Published var address = ""
    @Published var preparing = false
    @Published var ready = false
    @Published var stopping = false
    @Published var external = false
    @Published var choices: [AVMediaSelectionOption] = []
    @Published var selectedSubtitle = -1
    @Published var subtitleStatus = "Subtitle tracks will appear after preparation."
    private var group: AVMediaSelectionGroup?
    private var referer = ""
    private var startTime: Double = 0
    private var helper: Process?
    private var generation = UUID()
    private var routeObserver: NSKeyValueObservation?
    private var itemObserver: NSKeyValueObservation?
    private var pendingEvents = Data()
    private var expectedSubtitles = 0
    private var retiringHelpers: [Process] = []

    init() {
        player.allowsExternalPlayback = true
        routeObserver = player.observe(\.isExternalPlaybackActive, options: [.initial, .new]) { [weak self] player, _ in
            let active = player.isExternalPlaybackActive
            Task { @MainActor in self?.external = active }
        }
    }
    func stop() {
        generation = UUID()
        if let active = helper, active.isRunning {
            retiringHelpers.append(active)
            stopping = true
            active.terminate()
            Task { @MainActor [weak self] in
                while active.isRunning { try? await Task.sleep(nanoseconds: 100_000_000) }
                guard let self else { return }
                self.retiringHelpers.removeAll { $0 === active }
                self.stopping = !self.retiringHelpers.isEmpty
                if !self.stopping && self.helper == nil { self.message = "Stopped. Prepared media removed." }
            }
        }
        helper = nil
        player.pause(); player.replaceCurrentItem(with: nil)
        ready = false; preparing = false; group = nil; choices = []; selectedSubtitle = -1
        subtitleStatus = "Subtitle tracks will appear after preparation."
        message = "Stopped. Prepared media is being removed."
    }
    func receive(_ url: URL) {
        if url.isFileURL {
            stop(); source = url.path; title = url.lastPathComponent; subtitles = []; referer = ""; startTime = 0
            message = "Ready to prepare the selected local file."
            return
        }
        guard url.scheme == "videobridge", url.host == "open", url.absoluteString.count < 64000,
              let value = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "data" })?.value else {
            message = "Invalid VideoBridge link."; return
        }
        var encoded = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let data = Data(base64Encoded: encoded), let request = try? JSONDecoder().decode(Handoff.self, from: data),
              Self.webURL(request.url), (request.subtitles ?? []).count <= 16,
              (request.subtitles ?? []).allSatisfy({ Self.webURL($0.url) }),
              request.referer == nil || Self.webURL(request.referer!) else {
            message = "The browser sent an invalid or unsupported source."; return
        }
        stop(); source = request.url; title = String((request.title ?? "Browser video").prefix(200))
        referer = request.referer ?? ""; subtitles = request.subtitles ?? []
        startTime = max(0, min(request.currentTime ?? 0, 86400))
        message = "Browser video received. Prepare it, then select Apple TV."
        NSApp.activate(ignoringOtherApps: true)
    }
    static func webURL(_ value: String) -> Bool {
        guard value.count <= 16000, let u = URL(string: value), ["http", "https"].contains(u.scheme ?? ""),
              u.host != nil, u.user == nil, u.password == nil else { return false }
        return !value.contains("\r") && !value.contains("\n")
    }
    func loadSubtitleTest() {
        guard let folder = Bundle.main.resourceURL?.appendingPathComponent("M0"),
              FileManager.default.fileExists(atPath: folder.appendingPathComponent("video.mp4").path) else {
            message = "Test fixture is not packaged in this build."; return
        }
        receive(folder.appendingPathComponent("video.mp4"))
        subtitles = [Subtitle(url: folder.appendingPathComponent("English.en.srt").path, label: "English", language: "en"),
                     Subtitle(url: folder.appendingPathComponent("Chinese.zh.srt").path, label: "中文", language: "zh")]
        message = "Two-language test loaded. Prepare video, choose Apple TV, then Play."
    }
    func chooseVideo() {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false
        panel.message = "Choose a local video, including MP4, MOV or MKV."
        if panel.runModal() == .OK, let url = panel.url { receive(url) }
    }
    func chooseSubtitles() {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = true
        panel.allowedContentTypes = ["srt", "vtt", "ass", "ssa"].compactMap { UTType(filenameExtension: $0) }
        if panel.runModal() == .OK {
            for url in panel.urls where !subtitles.contains(where: { $0.url == url.path }) {
                subtitles.append(Subtitle(url: url.path, label: url.deletingPathExtension().lastPathComponent, language: "und"))
            }
        }
    }
    func prepare() {
        guard !stopping else { message = "Finishing previous session cleanup…"; return }
        let chosen = source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.webURL(chosen) || (chosen.hasPrefix("/") && FileManager.default.fileExists(atPath: chosen)) else {
            message = "Choose a local file or enter a direct HTTP(S) video URL."; return
        }
        stop()
        guard !stopping else { message = "Finishing previous session cleanup. Prepare again when it completes."; return }
        source = chosen; warning = ""; preparing = true; pendingEvents = Data()
        let id = generation
        guard let resource = Bundle.main.url(forResource: "relay", withExtension: "py") else {
            message = "The media preparation helper is missing."; preparing = false; return
        }
        let candidates = ["/Library/Frameworks/Python.framework/Versions/3.14/bin/python3", "/opt/homebrew/bin/python3", "/usr/bin/python3"]
        guard let python = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else {
            message = "Python 3 is required for this prototype."; preparing = false; return
        }
        let process = Process(); process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-u", resource.path]
        let input = Pipe(), output = Pipe()
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            Task { @MainActor in
                guard let self, self.generation == id else { return }
                self.pendingEvents.append(data)
                while let index = self.pendingEvents.firstIndex(of: 10) {
                    let line = self.pendingEvents.prefix(upTo: index)
                    self.pendingEvents.removeSubrange(...index)
                    if let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { self.event(event, id: id) }
                }
            }
        }
        process.terminationHandler = { [weak self] proc in
            Task { @MainActor in
                guard let self, self.generation == id else { return }
                self.preparing = false; self.ready = false; self.helper = nil
                self.player.pause(); self.player.replaceCurrentItem(with: nil)
                if !self.message.hasPrefix("Error:") {
                    self.message = "Error: media preparation stopped."
                }
            }
        }
        let tracks = subtitles.map { ["url": $0.url, "label": $0.label, "language": $0.language ?? "und"] }
        let request: [String: Any] = ["source": chosen, "subtitles": tracks, "referer": referer, "address": address]
        do {
            let data = try JSONSerialization.data(withJSONObject: request)
            try process.run(); helper = process
            input.fileHandleForWriting.write(data + Data([10])); try? input.fileHandleForWriting.close()
            message = "Inspecting media…"
        } catch { preparing = false; message = "Could not launch the media preparation helper." }
    }
    private func event(_ event: [String: Any], id: UUID) {
        switch event["event"] as? String {
        case "status": message = event["message"] as? String ?? "Preparing…"
        case "warning": warning = event["message"] as? String ?? ""
        case "error":
            player.pause(); ready = false; preparing = false
            message = "Error: " + (event["message"] as? String ?? "Preparation failed.")
        case "complete": message = event["message"] as? String ?? "Prepared."
        case "ready":
            guard let raw = event["url"] as? String, let url = URL(string: raw) else { return }
            expectedSubtitles = (event["subtitles"] as? [[String: Any]])?.count ?? 0
            let item = AVPlayerItem(url: url)
            itemObserver = item.observe(\.status, options: [.new]) { [weak self] item, _ in
                Task { @MainActor in
                    guard let self, self.generation == id else { return }
                    if item.status == .failed {
                        self.message = "Error: player could not load the prepared stream. Check the LAN address or VPN."
                        self.ready = false
                    } else if item.status == .readyToPlay {
                        self.ready = true; self.preparing = false
                        self.message = "Ready. Select Apple TV with the AirPlay button, then press Play."
                        await self.loadSubtitles(item, id: id)
                        if self.startTime > 0 {
                            self.message += " Browser position: \(Int(self.startTime)) seconds; seek there once that part is prepared."
                        }
                    }
                }
            }
            player.replaceCurrentItem(with: item)
        default: break
        }
    }
    private func loadSubtitles(_ item: AVPlayerItem, id: UUID) async {
        do {
            let loaded = try await item.asset.loadMediaSelectionGroup(for: .legible)
            guard generation == id else { return }
            group = loaded; choices = loaded?.options ?? []
            if expectedSubtitles > 0 && choices.count < expectedSubtitles {
                warning = "Expected \(expectedSubtitles) subtitle tracks, but the player found \(choices.count). Verify before watching."
            }
            subtitleStatus = choices.isEmpty ? "No selectable subtitle tracks found." : "\(choices.count) selectable subtitle track(s). TV display still requires verification."
            selectedSubtitle = choices.isEmpty ? -1 : 0; selectSubtitle()
        } catch { if generation == id { subtitleStatus = "Could not verify selectable subtitle tracks." } }
    }
    func selectSubtitle() {
        guard let item = player.currentItem, let group else { return }
        let requested = choices.indices.contains(selectedSubtitle) ? choices[selectedSubtitle] : nil
        item.select(requested, in: group)
        let actual = item.currentMediaSelection.selectedMediaOption(in: group)
        if actual == requested {
            subtitleStatus = "Selected: " + (actual?.displayName ?? "Off") + ". Verify the matching captions on TV."
        } else {
            subtitleStatus = "The player has not confirmed the selected subtitle track."
        }
    }
}

struct NativeVideo: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView(); view.player = player; view.controlsStyle = .floating
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
}
struct RoutePicker: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView(); view.player = player
        view.setAccessibilityLabel("Choose Apple TV or another AirPlay receiver")
        return view
    }
    func updateNSView(_ view: AVRoutePickerView, context: Context) { view.player = player }
}
struct ContentView: View {
    @StateObject private var model = Playback.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading) {
                    Text("VideoBridge").font(.largeTitle.bold())
                    Text("Video on your TV. Your Mac stays yours.").foregroundStyle(.secondary)
                }
                Spacer()
                Text(model.external ? "AirPlay active" : "Local playback").foregroundStyle(model.external ? .green : .secondary)
                RoutePicker(player: model.player).frame(width: 44, height: 38)
            }
            HStack {
                Button("Open video…", action: model.chooseVideo)
                Button("Load subtitle test", action: model.loadSubtitleTest).disabled(model.preparing || model.ready || model.stopping)
                TextField("Direct video URL or local file", text: $model.source).textFieldStyle(.roundedBorder)
                    .disabled(model.preparing || model.ready)
                Button("Prepare video", action: model.prepare).disabled(model.source.isEmpty || model.preparing || model.ready || model.stopping)
            }
            Text("Prototype: 8-bit H.264 SDR video only. Audio is converted to stereo AAC.")
                .font(.caption).foregroundStyle(.secondary)
            Text(model.title).lineLimit(1).font(.headline)
            NativeVideo(player: model.player).frame(minHeight: 270).background(.black)
            HStack {
                Button("Play") { model.player.play() }.disabled(!model.ready)
                Button("Pause") { model.player.pause() }.disabled(!model.ready)
                Button("Stop", action: model.stop)
                Spacer()
                if model.preparing { ProgressView().controlSize(.small) }
            }
            Text(model.message).font(.callout).textSelection(.enabled)
            if !model.warning.isEmpty { Text(model.warning).font(.callout).foregroundStyle(.orange) }
            Divider()
            HStack {
                Button("Add subtitles…", action: model.chooseSubtitles).disabled(model.preparing || model.ready)
                Text("\(model.subtitles.count) external track(s)").foregroundStyle(.secondary)
                if !model.subtitles.isEmpty {
                    Button("Clear") { model.subtitles = [] }.disabled(model.preparing || model.ready)
                }
                Spacer()
                Picker("Subtitles", selection: $model.selectedSubtitle) {
                    Text("Off").tag(-1)
                    ForEach(Array(model.choices.enumerated()), id: \.offset) { i, option in
                        Text(option.displayName).tag(i)
                    }
                }.frame(width: 260).disabled(!model.ready)
                    .onChange(of: model.selectedSubtitle) { _ in model.selectSubtitle() }
            }
            Text(model.subtitleStatus).font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Connection and format notes") {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("Mac LAN IPv4 address (blank = automatic)", text: $model.address).textFieldStyle(.roundedBorder)
                        .disabled(model.preparing || model.ready)
                    Text("Keep this app running and the Mac awake. Apple TV must reach this Mac on the local network. Preparation serves only this session’s media through a temporary private URL. Seeking ahead becomes available as preparation progresses.")
                    Text("Text subtitles stay selectable. ASS styling is simplified; image subtitles are not supported yet. Audio is converted to stereo AAC. DRM, live streams and browser-only login sessions are not supported in this prototype.")
                }.font(.caption).foregroundStyle(.secondary)
            }
        }.padding(22).frame(minWidth: 740, minHeight: 660)
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            guard let first = providers.first else { return false }
            _ = first.loadObject(ofClass: URL.self) { url, _ in
                if let url { Task { @MainActor in model.receive(url) } }
            }
            return true
        }
    }
}
class AppDelegate: NSObject, NSApplicationDelegate {
    func application(_ application: NSApplication, open urls: [URL]) {
        Task { @MainActor in if let url = urls.first { Playback.shared.receive(url) } }
    }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { Playback.shared.stop() }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}
@main struct VideoBridgeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup { ContentView() }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Open video…") { Playback.shared.chooseVideo() }.keyboardShortcut("o")
            }
        }
    }
}
