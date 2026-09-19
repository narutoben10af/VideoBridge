import SwiftUI
import AppKit
import AVKit
import UniformTypeIdentifiers
import Darwin

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

enum InboxError: Error { case invalid }

/// Launch Services receives only an opaque ID; media URLs stay in the private inbox.
struct BrowserInbox {
    static func consume(_ url: URL) throws -> Handoff {
        guard url.scheme == "videobridge", url.host == "request", url.user == nil,
              url.password == nil, url.port == nil, url.query == nil, url.fragment == nil,
              url.pathComponents.count == 2 else { throw InboxError.invalid }
        let token = url.lastPathComponent
        guard token.range(of: "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", options: .regularExpression) != nil else {
            throw InboxError.invalid
        }
        var directory = open(FileManager.default.homeDirectoryForCurrentUser.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard directory >= 0 else { throw InboxError.invalid }
        defer { close(directory) }
        for component in ["Library", "Application Support", "VideoBridge", "Inbox"] {
            let next = openat(directory, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
            guard next >= 0 else { throw InboxError.invalid }
            close(directory); directory = next
        }
        var directoryInfo = stat()
        guard fstat(directory, &directoryInfo) == 0, directoryInfo.st_uid == getuid(),
              directoryInfo.st_mode & 0o7777 == 0o700 else { throw InboxError.invalid }
        let filename = token + ".json"
        let descriptor = openat(directory, filename, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw InboxError.invalid }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFREG, info.st_mode & 0o7777 == 0o600,
              info.st_nlink == 1, info.st_size > 0, info.st_size <= 65536 else { throw InboxError.invalid }
        // Consume once, even when parsing subsequently fails.
        guard unlinkat(directory, filename, 0) == 0,
              let data = try handle.read(upToCount: 65537), data.count <= 65536,
              let envelope = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(envelope.keys) == Set(["protocolVersion", "requestId", "type", "payload", "createdAt"]),
              let version = envelope["protocolVersion"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(), version.doubleValue == 1,
              envelope["requestId"] as? String == token, envelope["type"] as? String == "offerMedia",
              let created = envelope["createdAt"] as? NSNumber,
              CFGetTypeID(created) != CFBooleanGetTypeID(), created.doubleValue.isFinite,
              Date().timeIntervalSince1970 - created.doubleValue >= -5,
              Date().timeIntervalSince1970 - created.doubleValue <= 60,
              let payload = envelope["payload"] as? [String: Any],
              Set(payload.keys) == Set(["url", "title", "referer", "currentTime", "subtitles"]),
              payload["title"] is String, payload["referer"] is String,
              let position = payload["currentTime"] as? NSNumber,
              CFGetTypeID(position) != CFBooleanGetTypeID(),
              position.doubleValue.isFinite, (0...86400).contains(position.doubleValue),
              let tracks = payload["subtitles"] as? [[String: Any]], tracks.count <= 16,
              tracks.allSatisfy({ Set($0.keys) == Set(["url", "label", "language"]) &&
                  ($0["language"] as? String)?.isEmpty == false }) else {
            throw InboxError.invalid
        }
        let request = try JSONDecoder().decode(Handoff.self, from: JSONSerialization.data(withJSONObject: payload))
        guard Playback.webURL(request.url), (request.title?.count ?? 0) <= 200,
              request.referer == nil || request.referer == "" || Playback.webURL(request.referer!),
              request.currentTime == nil || (request.currentTime!.isFinite && (0...86400).contains(request.currentTime!)),
              (request.subtitles ?? []).allSatisfy({ Playback.webURL($0.url) && $0.label.count <= 100 && ($0.language?.count ?? 0) <= 35 }) else {
            throw InboxError.invalid
        }
        return request
    }
}

/// UI policy stays separate from AVPlayer so boundary cases can be checked deterministically.
enum TimelinePolicy {
    static func canSeek(_ seconds: Double, in ranges: [Range<Double>]) -> Bool {
        seconds.isFinite && seconds >= 0 && ranges.contains(where: { $0.contains(seconds) })
    }
    static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0, seconds < Double(Int.max) else { return "–:––" }
        let whole = Int(seconds)
        if whole >= 3600 { return String(format: "%d:%02d:%02d", whole / 3600, whole / 60 % 60, whole % 60) }
        return String(format: "%d:%02d", whole / 60, whole % 60)
    }
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
    @Published var playing = false
    @Published var buffering = false
    @Published private(set) var currentSeconds: Double = 0
    @Published private(set) var durationSeconds: Double = 0
    @Published private(set) var seekableRanges: [Range<Double>] = []
    @Published var incoming: Handoff?
    @Published var choices: [AVMediaSelectionOption] = []
    @Published var selectedSubtitle = -1
    @Published var subtitleStatus = "Subtitle tracks will appear after preparation."
    private var group: AVMediaSelectionGroup?
    private var referer = ""
    @Published private(set) var startTime: Double = 0
    private var helper: Process?
    private var generation = UUID()
    private var routeObserver: NSKeyValueObservation?
    private var playbackObserver: NSKeyValueObservation?
    private var timeObserver: Any?
    private var itemObserver: NSKeyValueObservation?
    private var seekableObserver: NSKeyValueObservation?
    private var pendingEvents = Data()
    private var expectedSubtitles = 0
    private var retiringHelpers: [Process] = []

    init() {
        player.allowsExternalPlayback = true
        playbackObserver = player.observe(\.timeControlStatus, options: [.initial, .new]) { [weak self] player, _ in
            let playing = player.timeControlStatus == .playing
            let buffering = player.timeControlStatus == .waitingToPlayAtSpecifiedRate
            Task { @MainActor in self?.playing = playing; self?.buffering = buffering }
        }
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.25, preferredTimescale: 600), queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refreshTimeline() }
        }
        routeObserver = player.observe(\.isExternalPlaybackActive, options: [.initial, .new]) { [weak self] player, _ in
            let active = player.isExternalPlaybackActive
            Task { @MainActor in self?.external = active }
        }
    }
    var canResumeBrowserPosition: Bool {
        ready && startTime > 0 && TimelinePolicy.canSeek(startTime, in: seekableRanges)
    }
    private func refreshTimeline() {
        guard let item = player.currentItem else { return }
        let now = player.currentTime().seconds
        if now.isFinite { currentSeconds = max(0, now) }
        seekableRanges = item.seekableTimeRanges.compactMap {
            let range = $0.timeRangeValue
            let start = range.start.seconds, end = CMTimeRangeGetEnd(range).seconds
            guard start.isFinite, end.isFinite, end > max(0, start) else { return nil }
            return max(0, start)..<end
        }
    }
    func seek(to seconds: Double) {
        refreshTimeline()
        guard ready, TimelinePolicy.canSeek(seconds, in: seekableRanges) else {
            message = "That position is not available yet. Wait for more video to prepare, then try again."
            return
        }
        let id = generation
        message = "Seeking to \(TimelinePolicy.clock(seconds))…"
        player.seek(to: CMTime(seconds: seconds, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            Task { @MainActor in
                guard let self, self.generation == id else { return }
                self.refreshTimeline()
                self.message = finished ? "Position: \(TimelinePolicy.clock(self.currentSeconds))." : "Seek did not complete. Try again when the video is ready."
            }
        }
    }
    func resumeBrowserPosition() { seek(to: startTime) }
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
        seekableObserver = nil
        ready = false; preparing = false; group = nil; choices = []; selectedSubtitle = -1
        playing = false; buffering = false; currentSeconds = 0; durationSeconds = 0; seekableRanges = []
        subtitleStatus = "Subtitle tracks will appear after preparation."
        message = "Stopped. Prepared media is being removed."
    }
    func receive(_ url: URL) {
        if url.isFileURL {
            stop(); incoming = nil; source = url.path; title = url.lastPathComponent; subtitles = []; referer = ""; startTime = 0; warning = ""
            message = "Ready to prepare the selected local file."
            return
        }
        do {
            let request = try BrowserInbox.consume(url)
            if ready || preparing || stopping || helper != nil {
                incoming = request
            } else {
                applyBrowserOffer(request)
            }
            NSApp.activate(ignoringOtherApps: true)
        } catch {
            warning = "This browser request is invalid, expired or already used. Send the video again from the extension."
        }
    }
    private func applyBrowserOffer(_ request: Handoff) {
        incoming = nil
        source = request.url; title = String((request.title ?? "Browser video").prefix(200))
        referer = request.referer ?? ""; subtitles = request.subtitles ?? []
        startTime = request.currentTime ?? 0; warning = ""
        message = "Browser video received. Prepare for TV, then choose your Apple TV and press Play."
    }
    func replaceWithIncoming() {
        guard let request = incoming else { return }
        incoming = nil; stop(); applyBrowserOffer(request)
    }
    nonisolated static func webURL(_ value: String) -> Bool {
        guard value.count <= 16000, let u = URL(string: value), u.scheme == "https", (u.port == nil || u.port == 443),
              u.host != nil, u.user == nil, u.password == nil else { return false }
        return !value.contains("\\") && value.unicodeScalars.allSatisfy {
            !CharacterSet.whitespacesAndNewlines.contains($0) && !CharacterSet.controlCharacters.contains($0)
        }
    }
    func loadSubtitleTest() {
        guard let folder = Bundle.main.resourceURL?.appendingPathComponent("M0"),
              FileManager.default.fileExists(atPath: folder.appendingPathComponent("video.mp4").path) else {
            message = "Test fixture is not packaged in this build."; return
        }
        guard !ready && !preparing && !stopping else {
            message = "Stop the current video before loading the subtitle test."; return
        }
        receive(folder.appendingPathComponent("video.mp4"))
        subtitles = [Subtitle(url: folder.appendingPathComponent("English.en.srt").path, label: "English", language: "en"),
                     Subtitle(url: folder.appendingPathComponent("Chinese.zh.srt").path, label: "中文", language: "zh")]
        message = "Two-language test loaded. Prepare video, choose Apple TV, then Play."
    }
    func editSource(_ value: String) {
        source = value; referer = ""; startTime = 0; subtitles = []; warning = ""
        title = value.hasPrefix("/") ? URL(fileURLWithPath: value).lastPathComponent : "Direct media source"
        message = "Source changed. Add any subtitle files, then prepare for TV."
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
            guard data.count < 65536 else {
                preparing = false; message = "This video request is too large. Remove some subtitle tracks and try again."; return
            }
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
            if let duration = event["duration"] as? Double, duration.isFinite, duration > 0 {
                durationSeconds = duration
            }
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
                        self.refreshTimeline()
                        self.message = "Ready. Select Apple TV with the AirPlay button, then press Play."
                        await self.loadSubtitles(item, id: id)
                        guard self.generation == id else { return }
                        if self.startTime > 0 {
                            self.message += " Resume at your browser position when it becomes available below."
                        }
                    }
                }
            }
            player.replaceCurrentItem(with: item)
            seekableObserver = item.observe(\.seekableTimeRanges, options: [.initial, .new]) { [weak self] _, _ in
                Task { @MainActor in
                    guard let self, self.generation == id else { return }
                    self.refreshTimeline()
                }
            }
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
    @State private var scrubbing = false
    @State private var scrubSeconds: Double = 0
    private var busy: Bool { model.preparing || model.ready || model.stopping }
    private var stateLabel: String {
        if model.stopping { return "Stopping…" }
        if model.preparing { return "Preparing video…" }
        if model.buffering { return "Buffering…" }
        if model.playing { return model.external ? "Playing on AirPlay" : "Playing · AirPlay not confirmed" }
        if model.ready { return model.external ? "AirPlay connected · Paused" : "Paused · Choose Apple TV" }
        return model.source.isEmpty ? "Choose a video to begin" : "Ready to prepare"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("VideoBridge").font(.largeTitle.bold())
                    Text("Video on your TV. Your Mac stays yours.").foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 5) {
                    Text(model.external ? "AirPlay connected" : "Choose Apple TV")
                        .font(.callout).foregroundStyle(model.external ? .green : .secondary)
                    RoutePicker(player: model.player).frame(width: 44, height: 34)
                }
            }
            if let incoming = model.incoming {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("New browser video").font(.headline)
                        Text(incoming.title?.isEmpty == false ? incoming.title! : "Browser video").lineLimit(1)
                        Text("Your current video continues until you replace it.").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Replace video", action: model.replaceWithIncoming)
                    Button("Dismiss") { model.incoming = nil }
                }.padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
            }
            HStack {
                Button("Open video…", action: model.chooseVideo).controlSize(.large)
                Text("or drop a file here · send a video from your browser extension")
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(model.source.isEmpty ? "No video selected" : model.title).font(.headline).lineLimit(1)
                Text("8-bit H.264 SDR video. Audio becomes stereo AAC. ASS subtitle styling is simplified.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            NativeVideo(player: model.player).frame(minHeight: 240).background(.black)
            if model.ready {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(TimelinePolicy.clock(scrubbing ? scrubSeconds : model.currentSeconds))
                            .monospacedDigit().frame(minWidth: 45, alignment: .leading)
                        Slider(value: Binding(get: {
                            min(max(0, scrubbing ? scrubSeconds : model.currentSeconds), max(1, model.durationSeconds))
                        }, set: { scrubSeconds = $0 }), in: 0...max(1, model.durationSeconds), onEditingChanged: { editing in
                            if editing {
                                scrubSeconds = model.currentSeconds; scrubbing = true
                            } else {
                                scrubbing = false; model.seek(to: scrubSeconds)
                            }
                        }).disabled(model.seekableRanges.isEmpty)
                            .accessibilityLabel("Video position")
                        Text(TimelinePolicy.clock(model.durationSeconds)).monospacedDigit()
                    }
                    HStack {
                        Text(model.seekableRanges.isEmpty ? "Waiting for seekable video…" :
                            "Seekable: " + model.seekableRanges.map { TimelinePolicy.clock($0.lowerBound) + "–" + TimelinePolicy.clock($0.upperBound) }.joined(separator: ", "))
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        if model.startTime > 0 {
                            Button("Resume at browser position (\(TimelinePolicy.clock(model.startTime)))", action: model.resumeBrowserPosition)
                                .disabled(!model.canResumeBrowserPosition)
                                .help(model.canResumeBrowserPosition ? "Seek to the position received from your browser." : "Waiting for this part of the video to become seekable.")
                        }
                    }
                }
            }
            HStack(spacing: 12) {
                if model.ready {
                    Button(model.playing || model.buffering ? "Pause" : "Play") {
                        if model.playing || model.buffering { model.player.pause() } else { model.player.play() }
                    }.buttonStyle(.borderedProminent).controlSize(.large)
                } else {
                    Button("Prepare for TV", action: model.prepare)
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .disabled(model.source.isEmpty || model.preparing || model.stopping)
                }
                Button("Stop", action: model.stop).disabled(!busy)
                if model.preparing || model.stopping || model.buffering { ProgressView().controlSize(.small) }
                Spacer()
                Text(stateLabel).font(.callout).foregroundStyle(.secondary)
            }
            Text(model.message).font(.callout).textSelection(.enabled)
            if !model.warning.isEmpty { Text(model.warning).font(.callout).foregroundStyle(.orange) }
            Divider()
            HStack {
                Text("Subtitles").font(.headline)
                Button("Add file…", action: model.chooseSubtitles).disabled(busy)
                if !model.subtitles.isEmpty {
                    Text("\(model.subtitles.count) added").foregroundStyle(.secondary)
                    Button("Clear") { model.subtitles = [] }.disabled(busy)
                }
                Spacer()
                Picker("Language", selection: $model.selectedSubtitle) {
                    Text("Off").tag(-1)
                    ForEach(Array(model.choices.enumerated()), id: \.offset) { i, option in
                        Text(option.displayName).tag(i)
                    }
                }.frame(width: 260).disabled(!model.ready)
                    .onChange(of: model.selectedSubtitle) { _ in model.selectSubtitle() }
            }
            Text(model.subtitleStatus).font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Source and connection settings") {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("Direct media URL or local file", text: Binding(get: { model.source }, set: { model.editSource($0) }))
                        .textFieldStyle(.roundedBorder).disabled(busy)
                    TextField("Mac LAN IPv4 address (blank = automatic)", text: $model.address)
                        .textFieldStyle(.roundedBorder).disabled(busy)
                    Text("Prepare the video, choose Apple TV above, then press Play. Keep VideoBridge running and your Mac awake. Your TV must reach this Mac on the local network. Seeking ahead is available as preparation progresses.")
                    Text("DRM, live streams, image subtitles and browser-only login sessions are not supported. The subtitle test clip is available from the File menu.")
                }.font(.caption).foregroundStyle(.secondary)
            }
        }.padding(22).frame(minWidth: 780, minHeight: 680)
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
                Divider()
                Button("Load subtitle test clip") { Playback.shared.loadSubtitleTest() }
            }
        }
    }
}
