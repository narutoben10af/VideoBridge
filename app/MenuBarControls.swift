import SwiftUI
import AVKit

/// Compact, native controls for the existing session; does not create a second player.
struct MenuBarControls: View {
    @ObservedObject var model: Playback
    let openMainWindow: () -> Void

    private var canControl: Bool { model.ready && !model.stopping && !model.reloading }
    private var canStop: Bool { (model.ready || model.preparing || model.reloading) && !model.stopping }
    private var pauseAvailable: Bool { model.playing || model.buffering }
    private var status: String {
        if model.stopping { return "Stopping…" }
        if model.reloading { return "Restoring prepared video…" }
        if model.preparing { return "Preparing video…" }
        if model.buffering { return "Buffering…" }
        if model.playing { return "Playing" }
        if model.ready { return "Paused" }
        return model.source.isEmpty ? "No video selected" : "Ready to prepare"
    }


    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "play.tv.fill")
                    .font(.title2).foregroundStyle(.tint)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text("VideoBridge").font(.headline)
                    Text(status).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if model.preparing || model.buffering || model.stopping || model.reloading {
                    ProgressView().controlSize(.small).accessibilityLabel(status)
                }
            }

            VStack(alignment: .leading, spacing: 7) {
                Text(model.source.isEmpty ? "Open a video to get started" : model.title)
                    .font(.callout.weight(.semibold)).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Label(model.external ? "AirPlay active" : "AirPlay not confirmed", systemImage: "airplayvideo")
                    .font(.caption).foregroundStyle(model.external ? .green : .secondary)
                    .accessibilityLabel(model.external ? "AirPlay external playback is active" : "AirPlay external playback is not confirmed")
                if model.ready {
                    Label(model.fullyPrepared ? "Fully prepared" : "Preparing remaining video…", systemImage: model.fullyPrepared ? "checkmark.circle" : "arrow.down.circle")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Text(TimelinePolicy.clock(model.currentSeconds))
                        Spacer()
                        Text(TimelinePolicy.clock(model.durationSeconds))
                    }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("Playback time")
                        .accessibilityValue("\(TimelinePolicy.clock(model.currentSeconds)) of \(TimelinePolicy.clock(model.durationSeconds))")
                }
            }

            HStack(spacing: 18) {
                Spacer()
                Button { model.skip(by: -10) } label: {
                    Image(systemName: "gobackward.10").font(.title2).frame(width: 38, height: 32)
                }.disabled(!model.canSkip(by: -10))
                    .accessibilityLabel("Back 10 seconds")
                    .help("Seek back 10 seconds when that position is available.")
                Button {
                    if pauseAvailable { model.player.pause() } else { model.player.play() }
                } label: {
                    Image(systemName: pauseAvailable ? "pause.fill" : "play.fill")
                        .font(.title2).frame(width: 44, height: 34)
                }.buttonStyle(.borderedProminent).controlSize(.large).disabled(!canControl)
                    .accessibilityLabel(pauseAvailable ? "Pause video" : "Play video")
                Button { model.skip(by: 10) } label: {
                    Image(systemName: "goforward.10").font(.title2).frame(width: 38, height: 32)
                }.disabled(!model.canSkip(by: 10))
                    .accessibilityLabel("Forward 10 seconds")
                    .help("Seek forward 10 seconds when that position is available.")
                Spacer()
            }.buttonStyle(.borderless)

            Picker("Subtitles", selection: Binding(get: { model.selectedSubtitle }, set: {
                model.selectedSubtitle = $0
                model.selectSubtitle()
            })) {
                Text("Off").tag(-1)
                ForEach(Array(model.choices.enumerated()), id: \.offset) { index, choice in
                    Text(choice.displayName).tag(index)
                }
            }.pickerStyle(.menu).disabled(!canControl || model.choices.isEmpty)
                .accessibilityLabel("Subtitle language")

            if model.message.hasPrefix("Error:") {
                Text(model.message)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Open playback details", action: openMainWindow)
            }

            if model.incoming != nil {
                Label("New browser video waiting", systemImage: "tray.and.arrow.down")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Review new video", action: openMainWindow)
            } else if !model.warning.isEmpty {
                Label("A playback notice needs your attention", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.secondary)
                Button("View notice", action: openMainWindow)
            }

            Divider()
            HStack {
                Button("Stop", action: model.stop).disabled(!canStop)
                    .help("Stop playback and remove this session’s prepared media.")
                Spacer()
                Button("Open VideoBridge", action: openMainWindow)
                    .accessibilityLabel("Open the main VideoBridge window")
            }
        }
        .padding(18)
        .frame(width: 340)
        .background(.regularMaterial)
    }
}
