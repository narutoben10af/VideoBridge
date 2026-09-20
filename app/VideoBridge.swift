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
    static func arrowSkip(keyCode: UInt16, modifiers: NSEvent.ModifierFlags, editingText: Bool) -> Double? {
        guard !editingText, modifiers.intersection([.command, .option, .control, .shift]).isEmpty else { return nil }
        if keyCode == 123 { return -10 }
        if keyCode == 124 { return 10 }
        return nil
    }
    static func skipTarget(from current: Double, by offset: Double, in ranges: [Range<Double>]) -> Double? {
        guard current.isFinite, offset.isFinite, offset != 0,
              let beginning = ranges.map(\.lowerBound).min() else { return nil }
        let proposed = current + offset
        let target = offset < 0 ? max(beginning, proposed) : proposed
        guard target != current, canSeek(target, in: ranges) else { return nil }
        return target
    }
    static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0, seconds < Double(Int.max) else { return "–:––" }
        let whole = Int(seconds)
        if whole >= 3600 { return String(format: "%d:%02d:%02d", whole / 3600, whole / 60 % 60, whole % 60) }
        return String(format: "%d:%02d", whole / 60, whole % 60)
    }
}

struct RecoverySnapshot {
    let position: Double
    let subtitle: Any?
    let subtitlesOff: Bool
    let wasPlaying: Bool
}

/// A failed replacement must not become the source of a subsequent retry's saved position.
struct RecoveryRetryState {
    private(set) var saved: RecoverySnapshot?
    mutating func begin(current: RecoverySnapshot) -> RecoverySnapshot {
        if saved == nil { saved = current }
        return saved!
    }
    mutating func updateSubtitle(_ property: Any?, off: Bool) {
        guard let old = saved else { return }
        saved = RecoverySnapshot(position: old.position, subtitle: property, subtitlesOff: off, wasPlaying: old.wasPlaying)
    }
    mutating func reset() { saved = nil }
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
    @Published private(set) var fullyPrepared = false
    @Published var stopping = false
    @Published private(set) var reloading = false
    @Published private(set) var diagnosticEvents: [String] = []
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
    private var seekRequest = UUID()
    private var pendingSeekTarget: Double?
    private var routeObserver: NSKeyValueObservation?
    private var playbackObserver: NSKeyValueObservation?
    private var timeObserver: Any?
    private var itemObserver: NSKeyValueObservation?
    private var seekableObserver: NSKeyValueObservation?
    private var pendingEvents = Data()
    private var expectedSubtitles = 0
    private var preparedURL: URL?
    private var recoveryTask: Task<Void, Never>?
    private var subtitleLoadFinished = false
    private var completionHandoffPending = false
    private var timelineInteractionActive = false
    private struct Recovery {
        let position: Double
        let subtitle: Any?
        let subtitlesOff: Bool
        let wasPlaying: Bool
        var seekStarted = false
    }
    private var recovery: Recovery?
    private var recoveryRetry = RecoveryRetryState()
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
            Task { @MainActor in
                self?.external = active
                self?.captureDiagnostic("route_changed")
            }
        }
    }
    var canResumeBrowserPosition: Bool {
        ready && !reloading && startTime > 0 && TimelinePolicy.canSeek(startTime, in: seekableRanges)
    }
    private func refreshTimeline() {
        guard let item = player.currentItem else { return }
        let now = player.currentTime().seconds
        if now.isFinite { currentSeconds = max(0, now) }
        let updated = item.seekableTimeRanges.compactMap { value -> Range<Double>? in
            let range = value.timeRangeValue
            let start = range.start.seconds, end = CMTimeRangeGetEnd(range).seconds
            guard start.isFinite, end.isFinite, end > max(0, start) else { return nil }
            return max(0, start)..<end
        }
        if updated != seekableRanges {
            seekableRanges = updated
            captureDiagnostic("ranges_changed")
        }
    }
    private func captureDiagnostic(_ event: String) {
        let item = player.currentItem
        func ranges(_ values: [NSValue]) -> String {
            values.prefix(4).map {
                let r = $0.timeRangeValue
                return TimelinePolicy.clock(r.start.seconds) + "-" + TimelinePolicy.clock(CMTimeRangeGetEnd(r).seconds)
            }.joined(separator: ",")
        }
        let error = item?.error as NSError?
        let knownDomains = [NSURLErrorDomain, AVFoundationErrorDomain, NSOSStatusErrorDomain]
        let domain = error.map { knownDomains.contains($0.domain) ? $0.domain : "other" } ?? "none"
        let waiting = player.reasonForWaitingToPlay?.rawValue ?? "none"
        let safeWaiting = waiting.range(of: "^[A-Za-z]{1,80}$", options: .regularExpression) != nil ? waiting : "other"
        let entry = "t=\(Int(ProcessInfo.processInfo.systemUptime)) \(event) item=\(item?.status.rawValue ?? -1) control=\(player.timeControlStatus.rawValue) external=\(external) position=\(TimelinePolicy.clock(player.currentTime().seconds)) seek=[\(ranges(item?.seekableTimeRanges ?? []))] loaded=[\(ranges(item?.loadedTimeRanges ?? []))] waiting=\(safeWaiting) error=\(domain):\(error?.code ?? 0) mediaError=\(item?.errorLog()?.events.last?.errorStatusCode ?? 0)"
        diagnosticEvents.append(entry)
        if diagnosticEvents.count > 32 { diagnosticEvents.removeFirst(diagnosticEvents.count - 32) }
    }
    func canSkip(by offset: Double) -> Bool {
        ready && !stopping && !reloading && TimelinePolicy.skipTarget(from: pendingSeekTarget ?? currentSeconds, by: offset, in: seekableRanges) != nil
    }
    func skip(by offset: Double) {
        refreshTimeline()
        guard ready, !stopping, !reloading,
              let target = TimelinePolicy.skipTarget(from: pendingSeekTarget ?? currentSeconds, by: offset, in: seekableRanges) else {
            message = "That skip is outside the available video. Wait for more preparation or choose a seekable position."
            return
        }
        seek(to: target)
    }
    func seek(to seconds: Double) {
        refreshTimeline()
        guard ready, !reloading, TimelinePolicy.canSeek(seconds, in: seekableRanges) else {
            message = "That position is not available yet. Wait for more video to prepare, then try again."
            return
        }
        recoveryRetry.reset()
        let id = generation
        let request = UUID(); seekRequest = request; pendingSeekTarget = seconds
        message = "Seeking to \(TimelinePolicy.clock(seconds))…"
        captureDiagnostic("seek_requested")
        player.seek(to: CMTime(seconds: seconds, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] finished in
            Task { @MainActor in
                guard let self, self.generation == id, self.seekRequest == request else { return }
                self.pendingSeekTarget = nil
                self.refreshTimeline()
                self.captureDiagnostic(finished ? "seek_finished" : "seek_failed")
                self.message = finished ? "Position: \(TimelinePolicy.clock(self.currentSeconds))." : "Seek did not complete. Try again when the video is ready."
                self.attemptCompletionHandoff()
            }
        }
    }
    func setTimelineInteractionActive(_ active: Bool) {
        timelineInteractionActive = active
        if !active { attemptCompletionHandoff() }
    }
    private func attemptCompletionHandoff() {
        guard completionHandoffPending, fullyPrepared, ready, subtitleLoadFinished,
              !timelineInteractionActive, pendingSeekTarget == nil, !reloading, !stopping,
              recoveryRetry.saved == nil, helper?.isRunning == true else { return }
        refreshTimeline()
        // An ended item has no resumable point in the half-open seekable range.
        guard currentSeconds < durationSeconds else {
            completionHandoffPending = false
            captureDiagnostic("completion_handoff_already_ended")
            return
        }
        completionHandoffPending = false
        captureDiagnostic("completion_handoff_requested")
        reloadPreparedVideo()
    }
    var canReloadPreparedVideo: Bool {
        fullyPrepared && preparedURL != nil && helper?.isRunning == true && !stopping && !reloading
    }
    func reloadPreparedVideo() {
        guard canReloadPreparedVideo, let url = preparedURL, let item = player.currentItem else { return }
        completionHandoffPending = false
        refreshTimeline()
        let option = group.flatMap { item.currentMediaSelection.selectedMediaOption(in: $0) }
        let saved = recoveryRetry.begin(current: RecoverySnapshot(position: pendingSeekTarget ?? currentSeconds, subtitle: option?.propertyList(), subtitlesOff: option == nil,
                                                                   wasPlaying: player.timeControlStatus != .paused))
        recovery = Recovery(position: saved.position, subtitle: saved.subtitle, subtitlesOff: saved.subtitlesOff, wasPlaying: saved.wasPlaying)
        seekRequest = UUID(); pendingSeekTarget = nil
        item.cancelPendingSeeks(); player.pause()
        reloading = true; ready = false; seekableRanges = []
        captureDiagnostic("reload_requested")
        message = "Reloading prepared video. Restoring position and subtitles before resuming…"
        let id = generation
        installPreparedItem(url, id: id)
        let replacement = player.currentItem
        recoveryTask?.cancel()
        recoveryTask = Task { @MainActor [weak self] in
            for _ in 0..<80 {
                guard let self, !Task.isCancelled, self.generation == id, self.reloading,
                      self.player.currentItem === replacement else { return }
                self.refreshTimeline()
                self.attemptRecovery(id: id)
                do { try await Task.sleep(nanoseconds: 250_000_000) } catch { return }
            }
            guard let self, self.generation == id, self.reloading, self.player.currentItem === replacement else { return }
            self.failRecovery("Reload timed out before the saved position became seekable. Playback is paused; choose an available position or try Reload again.")
        }
    }
    private func failRecovery(_ reason: String) {
        seekRequest = UUID(); pendingSeekTarget = nil
        recoveryTask?.cancel(); recoveryTask = nil; recovery = nil; reloading = false
        player.currentItem?.cancelPendingSeeks(); player.pause()
        captureDiagnostic("reload_failed")
        message = "Error: " + reason
    }
    private func attemptRecovery(id: UUID) {
        guard reloading, ready, subtitleLoadFinished, var saved = recovery, !saved.seekStarted,
              let item = player.currentItem, TimelinePolicy.canSeek(saved.position, in: seekableRanges) else { return }
        if let group {
            if saved.subtitlesOff { selectedSubtitle = -1; item.select(nil, in: group) }
            else if let property = saved.subtitle, let option = group.mediaSelectionOption(withPropertyList: property),
                    let index = choices.firstIndex(of: option) {
                selectedSubtitle = index; item.select(option, in: group)
            } else { failRecovery("The saved subtitle track could not be restored. Playback remains paused; choose a subtitle before playing."); return }
            let actual = item.currentMediaSelection.selectedMediaOption(in: group)
            let expected = choices.indices.contains(selectedSubtitle) ? choices[selectedSubtitle] : nil
            guard actual == expected else { return }
        } else if !saved.subtitlesOff {
            failRecovery("The saved subtitle track is unavailable after reload. Playback remains paused."); return
        }
        saved.seekStarted = true; recovery = saved
        let request = UUID(); seekRequest = request
        player.seek(to: CMTime(seconds: saved.position, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { [weak self, weak item] finished in
            Task { @MainActor in
                guard let self, let item, self.generation == id, self.seekRequest == request,
                      self.player.currentItem === item, self.reloading else { return }
                self.refreshTimeline()
                guard finished, abs(self.currentSeconds - saved.position) <= 0.1 else {
                    self.failRecovery("Reloaded video could not restore the saved position. Playback remains paused; choose an available position."); return
                }
                self.recoveryTask?.cancel(); self.recoveryTask = nil; self.recovery = nil; self.recoveryRetry.reset(); self.reloading = false
                self.selectSubtitle()
                if saved.wasPlaying { self.player.play() }
                self.captureDiagnostic("reload_succeeded")
                self.message = "Prepared video reloaded at \(TimelinePolicy.clock(self.currentSeconds)). Check the AirPlay route and captions on your TV."
            }
        }
    }
    func resumeBrowserPosition() { seek(to: startTime) }
    func stop() {
        generation = UUID(); seekRequest = UUID(); pendingSeekTarget = nil; fullyPrepared = false
        completionHandoffPending = false; timelineInteractionActive = false
        recoveryTask?.cancel(); recoveryTask = nil; recovery = nil; recoveryRetry.reset(); reloading = false; preparedURL = nil
        diagnosticEvents = []
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
        guard !reloading else { message = "Wait for reload to finish, or press Stop to end this session."; return }
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
                self.preparing = false; self.ready = false; self.helper = nil; self.preparedURL = nil
                self.completionHandoffPending = false
                if self.reloading { self.failRecovery("The media helper stopped during reload. Prepare the video again.") }
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
            completionHandoffPending = false
            if reloading { failRecovery("The media helper failed during reload. Prepare the video again.") }
            player.pause(); ready = false; preparing = false
            message = "Error: " + (event["message"] as? String ?? "Preparation failed.")
        case "complete":
            fullyPrepared = true
            message = event["message"] as? String ?? "Prepared."
            attemptCompletionHandoff()
        case "ready":
            guard let raw = event["url"] as? String, let url = URL(string: raw) else { return }
            if let duration = event["duration"] as? Double, duration.isFinite, duration > 0 {
                durationSeconds = duration
            }
            let initiallyComplete = event["fullyPrepared"] as? Bool ?? false
            fullyPrepared = fullyPrepared || initiallyComplete
            completionHandoffPending = !initiallyComplete
            expectedSubtitles = (event["subtitles"] as? [[String: Any]])?.count ?? 0
            preparedURL = url
            installPreparedItem(url, id: id)
        default: break
        }
    }
    private func installPreparedItem(_ url: URL, id: UUID) {
        itemObserver = nil; seekableObserver = nil
        subtitleLoadFinished = false; group = nil; choices = []; selectedSubtitle = -1
        let item = AVPlayerItem(url: url)
        player.replaceCurrentItem(with: item)
        itemObserver = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
            Task { @MainActor in
                guard let self, self.generation == id, self.player.currentItem === item else { return }
                if item.status == .failed {
                    self.captureDiagnostic("item_failed")
                    if self.reloading { self.failRecovery("Reload could not load the prepared stream. Check the local connection and try again.") }
                    else { self.message = "Error: player could not load the prepared stream. Check the LAN address or VPN." }
                    self.ready = false; self.preparing = false
                } else if item.status == .readyToPlay {
                    self.ready = true; self.preparing = false
                    self.refreshTimeline(); self.captureDiagnostic("item_ready")
                    if !self.reloading && self.recoveryRetry.saved == nil { self.message = "Ready. Select Apple TV with the AirPlay button, then press Play." }
                    await self.loadSubtitles(item, id: id)
                    guard self.generation == id, self.player.currentItem === item else { return }
                    if !self.reloading && self.recoveryRetry.saved == nil && self.startTime > 0 {
                        self.message += " Resume at your browser position when it becomes available below."
                    }
                }
            }
        }
        seekableObserver = item.observe(\.seekableTimeRanges, options: [.initial, .new]) { [weak self, weak item] _, _ in
            Task { @MainActor in
                guard let self, let item, self.generation == id, self.player.currentItem === item else { return }
                self.refreshTimeline()
            }
        }
    }
    private func loadSubtitles(_ item: AVPlayerItem, id: UUID) async {
        do {
            let loaded = try await item.asset.loadMediaSelectionGroup(for: .legible)
            guard generation == id, player.currentItem === item else { return }
            group = loaded; choices = loaded?.options ?? []
            if expectedSubtitles > 0 && choices.count < expectedSubtitles {
                warning = "Expected \(expectedSubtitles) subtitle tracks, but the player found \(choices.count). Verify before watching."
            }
            subtitleStatus = choices.isEmpty ? "No selectable subtitle tracks found." : "\(choices.count) selectable subtitle track(s). TV display still requires verification."
            subtitleLoadFinished = true
            if !reloading && recoveryRetry.saved == nil { selectedSubtitle = choices.isEmpty ? -1 : 0; selectSubtitle() }
            attemptCompletionHandoff()
        } catch {
            if generation == id, player.currentItem === item {
                completionHandoffPending = false
                subtitleStatus = "Could not verify selectable subtitle tracks."
                warning = "Automatic completion handoff could not verify subtitles. Reload prepared video manually when available."
                if reloading { failRecovery("Subtitle tracks could not be verified after reload. Playback remains paused.") }
            }
        }
    }
    func selectSubtitle() {
        guard let item = player.currentItem, let group else { return }
        let requested = choices.indices.contains(selectedSubtitle) ? choices[selectedSubtitle] : nil
        item.select(requested, in: group)
        if !reloading { recoveryRetry.updateSubtitle(requested?.propertyList(), off: requested == nil) }
        let actual = item.currentMediaSelection.selectedMediaOption(in: group)
        if actual == requested {
            subtitleStatus = "Selected: " + (actual?.displayName ?? "Off") + ". Verify the matching captions on TV."
        } else {
            subtitleStatus = "The player has not confirmed the selected subtitle track."
        }
    }
}

struct NativeVideo: NSViewRepresentable {
    let model: Playback
    func makeCoordinator() -> Coordinator { Coordinator(model: model) }
    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView(); view.player = model.player; view.controlsStyle = .none
        context.coordinator.view = view
        return view
    }
    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== model.player { view.player = model.player }
        let controls: AVPlayerViewControlsStyle = .none
        if view.controlsStyle != controls { view.controlsStyle = controls }
    }
    static func dismantleNSView(_ view: AVPlayerView, coordinator: Coordinator) { coordinator.removeMonitor() }

    /// Override AVPlayerView's built-in arrow behavior only for this window's bare arrows.
    final class Coordinator {
        weak var view: AVPlayerView?
        private var monitor: Any?
        init(model: Playback) {
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self, weak model] event in
                guard let model, let window = self?.view?.window, event.window === window else { return event }
                let editingText = window.firstResponder is NSTextView || window.firstResponder is NSTextField
                let suppressPlaybackKey = MainActor.assumeIsolated { model.reloading } && !editingText &&
                    [UInt16(49), 123, 124].contains(event.keyCode) && event.modifierFlags.intersection([.command, .option, .control, .shift]).isEmpty
                if suppressPlaybackKey { return nil }
                guard let offset = TimelinePolicy.arrowSkip(keyCode: event.keyCode, modifiers: event.modifierFlags,
                          editingText: window.firstResponder is NSTextView || window.firstResponder is NSTextField) else { return event }
                let consumed = MainActor.assumeIsolated {
                    guard model.ready && !model.stopping else { return false }
                    model.skip(by: offset)
                    return true
                }
                return consumed ? nil : event
            }
        }
        func removeMonitor() {
            if let monitor { NSEvent.removeMonitor(monitor); self.monitor = nil }
        }
        deinit { removeMonitor() }
    }
}
struct RoutePicker: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView(); view.player = player
        view.setAccessibilityLabel("Choose Apple TV or another AirPlay receiver")
        return view
    }
    func updateNSView(_ view: AVRoutePickerView, context: Context) {
        if view.player !== player { view.player = player }
    }
}
struct ContentView: View {
    @StateObject private var model = Playback.shared
    @State private var scrubbing = false
    @State private var scrubSeconds: Double = 0
    @State private var dropTargeted = false
    private var busy: Bool { model.preparing || model.ready || model.stopping || model.reloading }
    private var working: Bool { model.preparing || model.stopping || model.reloading || model.buffering }
    private var stateLabel: String {
        if model.stopping { return "Stopping…" }
        if model.reloading { return "Restoring playback…" }
        if model.preparing { return "Preparing video…" }
        if model.buffering { return "Buffering…" }
        if model.playing { return model.external ? "Playing on AirPlay" : "Playing on this Mac" }
        if model.ready { return model.external ? "Paused on AirPlay" : "Paused on this Mac" }
        return model.source.isEmpty ? "Open a video to begin" : "Ready to prepare"
    }
    private var hasError: Bool { model.message.hasPrefix("Error:") }
    var body: some View {
        VStack(spacing: 0) {
            if let incoming = model.incoming {
                HStack(spacing: 12) {
                    Image(systemName: "tray.and.arrow.down").foregroundStyle(.secondary)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("New browser video").font(.caption).foregroundStyle(.secondary)
                        Text(incoming.title?.isEmpty == false ? incoming.title! : "Browser video").font(.callout.weight(.medium)).lineLimit(2)
                        Text("Current playback continues until you replace it.").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Replace video", action: model.replaceWithIncoming)
                    Button("Dismiss") { model.incoming = nil }
                }.padding(14).background(.quaternary)
                Divider()
            }
            NativeVideo(model: model)
                .frame(minHeight: 260, maxHeight: .infinity)
                .background(.black)
                .overlay {
                    if model.source.isEmpty {
                        VStack(spacing: 14) {
                            Image(systemName: "play.tv").font(.system(size: 42, weight: .light)).accessibilityHidden(true)
                            Text("Your video. Your Apple TV.").font(.title2.weight(.semibold))
                            Text("Drop a video here, or send one from your browser extension.")
                                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                            Button("Open video…", action: model.chooseVideo).buttonStyle(.borderedProminent).controlSize(.large)
                        }.frame(maxWidth: .infinity, maxHeight: .infinity)
                            .background(Color(nsColor: .windowBackgroundColor))
                    }
                }
                .overlay { if dropTargeted { RoundedRectangle(cornerRadius: 8).strokeBorder(.tint, lineWidth: 3).padding(6).allowsHitTesting(false) } }
                .layoutPriority(1)
            ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(model.source.isEmpty ? "VideoBridge" : model.title).font(.headline).lineLimit(2)
                        HStack(spacing: 6) {
                            if working { ProgressView().controlSize(.small).accessibilityLabel(stateLabel) }
                            Text(stateLabel).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Spacer()
                    if model.ready {
                        Label(model.fullyPrepared ? "Fully prepared" : "Preparing remaining video", systemImage: model.fullyPrepared ? "checkmark.circle" : "arrow.down.circle")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if model.ready {
                    HStack(spacing: 10) {
                        Text(TimelinePolicy.clock(scrubbing ? scrubSeconds : model.currentSeconds)).monospacedDigit().frame(minWidth: 44, alignment: .leading)
                        Slider(value: Binding(get: {
                            min(max(0, scrubbing ? scrubSeconds : model.currentSeconds), max(1, model.durationSeconds))
                        }, set: { scrubSeconds = $0 }), in: 0...max(1, model.durationSeconds), onEditingChanged: { editing in
                            if editing {
                                scrubSeconds = model.currentSeconds; scrubbing = true; model.setTimelineInteractionActive(true)
                            } else {
                                scrubbing = false; model.seek(to: scrubSeconds); model.setTimelineInteractionActive(false)
                            }
                        }).disabled(model.seekableRanges.isEmpty || model.reloading).accessibilityLabel("Video position")
                        Text(TimelinePolicy.clock(model.durationSeconds)).monospacedDigit()
                    }.font(.caption)
                }
                HStack(spacing: 18) {
                    Button("Stop", action: model.stop).disabled(!busy).help("End this session and remove prepared media.")
                    Spacer()
                    if model.ready {
                        Button { model.skip(by: -10) } label: { Image(systemName: "gobackward.10").font(.title2).frame(width: 38, height: 32) }
                            .disabled(!model.canSkip(by: -10)).accessibilityLabel("Back 10 seconds").help("Back 10 seconds (Left Arrow).")
                        Button {
                            if model.playing || model.buffering { model.player.pause() } else { model.player.play() }
                        } label: { Image(systemName: model.playing || model.buffering ? "pause.fill" : "play.fill").font(.title2).frame(width: 44, height: 34) }
                            .buttonStyle(.borderedProminent).controlSize(.large).disabled(model.reloading)
                            .accessibilityLabel(model.playing || model.buffering ? "Pause video" : "Play video")
                        Button { model.skip(by: 10) } label: { Image(systemName: "goforward.10").font(.title2).frame(width: 38, height: 32) }
                            .disabled(!model.canSkip(by: 10)).accessibilityLabel("Forward 10 seconds").help("Forward 10 seconds (Right Arrow).")
                    } else {
                        Button("Prepare for TV", action: model.prepare).buttonStyle(.borderedProminent).controlSize(.large)
                            .disabled(model.source.isEmpty || model.preparing || model.stopping || model.reloading)
                    }
                    Spacer()
                    Picker("Subtitles", selection: $model.selectedSubtitle) {
                        Text("Off").tag(-1)
                        ForEach(Array(model.choices.enumerated()), id: \.offset) { i, option in Text(option.displayName).tag(i) }
                    }.pickerStyle(.menu).frame(maxWidth: 210).disabled(!model.ready || model.reloading)
                        .onChange(of: model.selectedSubtitle) { _ in model.selectSubtitle() }
                }
                if !model.source.isEmpty && !busy {
                    Text("8-bit H.264 SDR video. Audio becomes stereo AAC. ASS subtitle styling is simplified.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if hasError {
                    Label(model.message, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(model.message).font(.caption).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                if !model.warning.isEmpty {
                    Label(model.warning, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Button("Add subtitles…", action: model.chooseSubtitles).disabled(busy)
                    if !model.subtitles.isEmpty {
                        Text("\(model.subtitles.count) added").font(.caption).foregroundStyle(.secondary)
                        Button("Clear") { model.subtitles = [] }.disabled(busy)
                    }
                    Spacer()
                    if model.ready && model.startTime > 0 {
                        Button("Resume at \(TimelinePolicy.clock(model.startTime))", action: model.resumeBrowserPosition)
                            .disabled(!model.canResumeBrowserPosition).help("Resume at the browser position when it is seekable.")
                    }
                    if model.canReloadPreparedVideo || model.reloading {
                        Button(model.reloading ? "Restoring…" : "Reload prepared video", action: model.reloadPreparedVideo)
                            .disabled(!model.canReloadPreparedVideo).help("Restore this session’s prepared video, position and subtitles.")
                    }
                }.controlSize(.small)
                DisclosureGroup("Details") {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            Text(model.subtitleStatus).font(.caption)
                            Text(model.seekableRanges.isEmpty ? "No seekable range reported yet." : "Seekable: " + model.seekableRanges.map { TimelinePolicy.clock($0.lowerBound) + "–" + TimelinePolicy.clock($0.upperBound) }.joined(separator: ", ")).font(.caption)
                            TextField("Direct media URL or local file", text: Binding(get: { model.source }, set: { model.editSource($0) })).textFieldStyle(.roundedBorder).disabled(busy)
                            TextField("Mac LAN IPv4 address (blank = automatic)", text: $model.address).textFieldStyle(.roundedBorder).disabled(busy)
                            Text("Prepare the video, choose Apple TV, then press Play. Keep VideoBridge running and your Mac awake. Your TV must reach this Mac on the local network.").font(.caption)
                            Text("DRM, live streams, image subtitles and browser-only login sessions are not supported. The subtitle test clip is available from the File menu.").font(.caption)
                            Text("Playback diagnostics").font(.caption.weight(.semibold))
                            Text(model.diagnosticEvents.isEmpty ? "No playback events recorded." : model.diagnosticEvents.joined(separator: "\n"))
                                .font(.caption.monospaced()).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            Text("Last 32 events, kept only in memory. No media URLs, file paths or tokens.").font(.caption)
                        }.foregroundStyle(.secondary).padding(.top, 8)
                    }.frame(maxHeight: 180)
                }.font(.caption)
            }.padding(20)
            }.frame(minHeight: 240, idealHeight: 320, maxHeight: 400)
        }.frame(minWidth: 760, minHeight: 640)
        .toolbar {
            ToolbarItem(placement: .automatic) { Button(action: model.chooseVideo) { Label("Open video", systemImage: "folder") }.help("Open a local video") }
            ToolbarItem(placement: .automatic) {
                HStack(spacing: 6) {
                    Text(model.external ? "AirPlay active" : "Choose Apple TV").font(.caption).foregroundStyle(.secondary)
                    RoutePicker(player: model.player).frame(width: 38, height: 30)
                }
            }
        }
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
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
struct MenuBarEntry: View {
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        MenuBarControls(model: Playback.shared) {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}
@main struct VideoBridgeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        Window("VideoBridge", id: "main") { ContentView() }
        .commands {
            CommandGroup(after: .newItem) {
                Button("Open video…") { Playback.shared.chooseVideo() }.keyboardShortcut("o")
                Divider()
                Button("Load subtitle test clip") { Playback.shared.loadSubtitleTest() }
            }
        }
        MenuBarExtra("VideoBridge", systemImage: "play.tv") {
            MenuBarEntry()
        }.menuBarExtraStyle(.window)
    }
}
