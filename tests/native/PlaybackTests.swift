struct PlaybackTestFailure: Error, CustomStringConvertible {
    let description: String
}

@main struct PlaybackTests {
    @MainActor static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw PlaybackTestFailure(description: message) }
    }
    @MainActor static func wait(_ label: String, timeout: Double = 25,
                                until predicate: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !predicate() {
            if Date() >= deadline { throw PlaybackTestFailure(description: "Timed out: " + label) }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
    }
    @MainActor static func selected(_ model: Playback) async throws -> AVMediaSelectionOption? {
        guard let item = model.player.currentItem,
              let group = try await item.asset.loadMediaSelectionGroup(for: .legible) else {
            throw PlaybackTestFailure(description: "Prepared item has no legible media group")
        }
        return item.currentMediaSelection.selectedMediaOption(in: group)
    }
    @MainActor static func run(_ model: Playback) async throws {
        guard let resources = Bundle.main.resourceURL else {
            throw PlaybackTestFailure(description: "Missing isolated test bundle resources")
        }
        // Suppress receiver routing and audible playback in the isolated harness.
        model.player.allowsExternalPlayback = false
        model.player.isMuted = true
        model.address = "127.0.0.1"
        model.receive(resources.appendingPathComponent("video.mp4"))
        model.subtitles = [Subtitle(url: resources.appendingPathComponent("en.srt").path, label: "English", language: "en"),
                           Subtitle(url: resources.appendingPathComponent("zh.srt").path, label: "中文", language: "zh")]
        model.prepare()
        if ProcessInfo.processInfo.environment["VIDEOBRIDGE_TEST_PROGRESSIVE"] == "1" {
            try await wait("progressive initial readiness") {
                model.ready && !model.fullyPrepared &&
                    model.choices.contains(where: { $0.extendedLanguageTag?.hasPrefix("en") == true }) &&
                    model.choices.contains(where: { $0.extendedLanguageTag?.hasPrefix("zh") == true }) &&
                    TimelinePolicy.canSeek(3, in: model.seekableRanges)
            }
            let progressiveItem = model.player.currentItem
            let progressivePlaying = ProcessInfo.processInfo.environment["VIDEOBRIDGE_TEST_PROGRESSIVE_PLAYING"] == "1"
            model.selectedSubtitle = progressivePlaying ? model.choices.firstIndex(where: {
                $0.extendedLanguageTag?.hasPrefix("zh") == true
            })! : -1
            model.selectSubtitle()
            model.setTimelineInteractionActive(true)
            if progressivePlaying {
                model.player.play()
                try await wait("progressive playback starts") { model.player.timeControlStatus == .playing }
            }
            try await wait("completion while timeline interaction is active", timeout: 40) { model.fullyPrepared }
            try require(model.player.currentItem === progressiveItem && !model.reloading,
                        "Completion interrupted an active timeline interaction")
            model.seek(to: 3)
            model.setTimelineInteractionActive(false)
            try await wait("automatic completion handoff", timeout: 40) {
                model.fullyPrepared && !model.reloading && model.player.currentItem !== progressiveItem &&
                    model.message.hasPrefix("Prepared video reloaded")
            }
            if progressivePlaying {
                try await wait("automatic handoff resumes playback") { model.player.timeControlStatus == .playing }
                let position = model.player.currentTime().seconds
                try require(position >= 2.85 && position < 5, "Playing completion handoff lost the pending seek position")
                try require(try await selected(model)?.extendedLanguageTag?.hasPrefix("zh") == true,
                            "Automatic playing handoff lost Chinese subtitles")
                model.player.pause()
            } else {
                try require(model.player.timeControlStatus == .paused, "Completion handoff started paused playback")
                try require(abs(model.player.currentTime().seconds - 3) < 0.15, "Completion handoff lost position")
                try require(try await selected(model) == nil && model.selectedSubtitle == -1,
                            "Completion handoff lost explicit subtitles Off")
            }
            let completedItem = model.player.currentItem
            // Longer than the observed ~12-second post-completion range collapse.
            try await Task.sleep(nanoseconds: 22_000_000_000)
            try require(model.player.currentItem === completedItem && !model.reloading,
                        "Completion triggered repeated automatic replacements")
            try require(TimelinePolicy.canSeek(20, in: model.seekableRanges),
                        "Completed replacement lost its seekable range")
            try require(model.diagnosticEvents.filter { $0.contains("completion_handoff_requested") }.count == 1,
                        "Expected exactly one automatic completion handoff")
            print("PASS progressive completion preserves position/\(progressivePlaying ? "playing Chinese" : "paused Off"), replaces once, and retains seekability beyond collapse window")
        }
        try await wait("production preparation and both subtitle tracks") {
            model.ready && model.fullyPrepared && model.choices.contains(where: { $0.extendedLanguageTag?.hasPrefix("en") == true }) &&
                model.choices.contains(where: { $0.extendedLanguageTag?.hasPrefix("zh") == true }) &&
                TimelinePolicy.canSeek(4, in: model.seekableRanges)
        }
        if ProcessInfo.processInfo.environment["VIDEOBRIDGE_TEST_PROGRESSIVE"] != "1" {
            let initiallyCompletedItem = model.player.currentItem
            try require(!model.diagnosticEvents.contains(where: { $0.contains("completion_handoff_requested") }),
                        "Already-completed initial media unexpectedly triggered a handoff")
            try await Task.sleep(nanoseconds: 22_000_000_000)
            try require(model.player.currentItem === initiallyCompletedItem && !model.reloading &&
                        TimelinePolicy.canSeek(4, in: model.seekableRanges), "Completed initial item did not stay stable")
            try require(!model.diagnosticEvents.contains(where: { $0.contains("completion_handoff_requested") }),
                        "Completed initial item triggered a delayed handoff")
            print("PASS initially completed media stays seekable without automatic replacement")
        }
        try require(model.canReloadPreparedVideo, "Completed relay must permit reload")
        model.selectedSubtitle = -1; model.selectSubtitle()
        try require(try await selected(model) == nil, "Subtitles Off not selected before reload")
        model.seek(to: 3)
        try await wait("seek before paused reload") { abs(model.currentSeconds - 3) < 0.1 }
        let pausedItem = model.player.currentItem
        let pausedPosition = model.player.currentTime().seconds
        model.reloadPreparedVideo()
        try require(model.reloading, "Reload did not enter restoring state")
        try require(model.player.currentItem !== pausedItem, "Reload did not replace AVPlayerItem")
        try await wait("paused reload restored") { !model.reloading && model.message.hasPrefix("Prepared video reloaded") }
        try require(model.player.timeControlStatus == .paused, "Paused reload unexpectedly started playback")
        try require(abs(model.player.currentTime().seconds - pausedPosition) < 0.15, "Paused reload lost position")
        try require(try await selected(model) == nil && model.selectedSubtitle == -1, "Reload lost subtitles Off")
        print("PASS production reload preserves paused position and subtitles Off")

        guard let chinese = model.choices.firstIndex(where: { $0.extendedLanguageTag?.hasPrefix("zh") == true }) else {
            throw PlaybackTestFailure(description: "Chinese subtitle option missing")
        }
        model.selectedSubtitle = chinese; model.selectSubtitle()
        let originalOption = try await selected(model)
        try require(originalOption != nil, "Second subtitle could not be selected")
        let language = originalOption?.extendedLanguageTag
        model.player.play()
        try await wait("local playback advances") { model.playing && model.currentSeconds > pausedPosition + 0.3 }
        let playingPosition = model.player.currentTime().seconds
        let playingItem = model.player.currentItem
        model.reloadPreparedVideo()
        try require(model.reloading && model.player.timeControlStatus == .paused, "Reload must pause during restoration")
        try await wait("playing reload restored") { !model.reloading && model.playing && model.message.hasPrefix("Prepared video reloaded") }
        try require(model.player.currentItem !== playingItem, "Playing reload did not replace item")
        let resumedPosition = model.player.currentTime().seconds
        try require(resumedPosition >= playingPosition - 0.15 && resumedPosition < playingPosition + 2,
                    "Playing reload did not preserve a nearby playback position")
        let restoredOption = try await selected(model)
        try require(restoredOption?.extendedLanguageTag == language && restoredOption != nil,
                    "Playing reload changed the selected subtitle language")
        print("PASS production reload preserves subtitle language and resumes playback")
    }
    @MainActor static func main() async {
        let model = Playback()
        var failed = false
        do { try await run(model) }
        catch {
            failed = true
            print("FAIL: \(error); state: \(model.message); ready=\(model.ready) complete=\(model.fullyPrepared) choices=\(model.choices.count) subtitles=\(model.subtitleStatus)")
            for event in model.diagnosticEvents { print(event) }
        }
        model.stop()
        do {
            try await wait("relay cleanup", timeout: 10) { !model.stopping }
            try require(model.player.currentItem == nil && !model.ready && !model.canReloadPreparedVideo,
                        "Stop retained an active playback item or reload capability")
            print("PASS production Stop after reload clears playback and finishes helper cleanup")
        } catch { failed = true; print("FAIL cleanup: \(error)") }
        exit(failed ? 1 : 0)
    }
}
