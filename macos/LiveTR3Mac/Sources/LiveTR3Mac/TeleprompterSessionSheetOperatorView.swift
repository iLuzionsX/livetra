import AppKit
import SwiftUI

struct TeleprompterSessionSheetOperatorView: View {
    @ObservedObject var session: SessionController
    @ObservedObject var sessionManager: SessionManager
    let onOpenProjector: () -> Void

    @State private var hudVisible = true
    @State private var settingsPresented = false
    @State private var partialPulseActive = false

    var body: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                if let workerStatus = session.transcript.workerStatus, workerStatus.state != .ready {
                    banner(text: workerStatus.message, tint: .orange)
                }

                if let error = displayedError {
                    banner(text: error, tint: .red)
                }

                cueReader
            }

            VStack(spacing: 10) {
                bottomHUD
                    .opacity(hudVisible || settingsPresented ? 1 : 0)
                    .animation(.easeInOut(duration: 0.2), value: hudVisible)
                projectorLookBar
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 22)
        }
        .background(Color.black.opacity(0.93))
        .onHover { hudVisible = $0 }
        .onAppear {
            session.refreshDevices()
        }
        .onChange(of: session.transcript.partialTickAt) { _, tick in
            guard tick != nil else { return }
            partialPulseActive = true
            Task {
                try? await Task.sleep(nanoseconds: 150_000_000)
                partialPulseActive = false
            }
        }
        .sheet(isPresented: $settingsPresented) {
            sessionControlsSheet
        }
        .background {
            TeleprompterShortcutMonitor(
                onStartStop: session.startStop,
                onExportTXT: exportTXT
            )
        }
    }

    private var displayedError: String? {
        session.transcript.lastError ?? session.error
    }

    private var cueReader: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: cueAlignment, spacing: 18) {
                    ForEach(session.transcript.entries) { entry in
                        cueBlock(for: entry)
                            .id(entry.id)
                    }
                }
                .padding(.horizontal, 48)
                .padding(.vertical, 48)
                .frame(maxWidth: .infinity, alignment: frameAlignment)
            }
            .environment(\.layoutDirection, isRtlLanguage(session.config.target_lang) ? .rightToLeft : .leftToRight)
            .onChange(of: session.transcript.entries.count) { _, _ in
                scrollToLatest(using: proxy)
            }
            .onChange(of: session.transcript.entries.last?.translation) { _, _ in
                scrollToLatest(using: proxy)
            }
            .overlay {
                if session.transcript.entries.isEmpty {
                    emptyState
                }
            }
        }
    }

    @ViewBuilder
    private func cueBlock(for entry: TranscriptUtterance) -> some View {
        VStack(alignment: cueAlignment, spacing: 6) {
            if !entry.original.isEmpty {
                Text(entry.original)
                    .font(.title3)
                    .foregroundStyle(.white.opacity(0.48))
                    .multilineTextAlignment(textAlignment)
            }

            if entry.translation.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text("Translating to \(session.config.target_lang)…")
                    .font(.title3)
                    .foregroundStyle(.white.opacity(0.48))
            } else {
                Text(entry.translation)
                    .font(.system(size: sessionManager.projectorFontSize, weight: .semibold))
                    .foregroundStyle(entry.state == .partial ? .white.opacity(0.72) : .white)
                    .multilineTextAlignment(textAlignment)
            }
        }
        .frame(maxWidth: .infinity, alignment: frameAlignment)
    }

    private var projectorLookBar: some View {
        HStack(spacing: 12) {
            Text("Projector")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)

            ProjectorLookPicker(selection: $sessionManager.projectorStyle)
                .frame(maxWidth: 280)

            Text(sessionManager.projectorStyle.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            Spacer(minLength: 8)

            Button(action: onOpenProjector) {
                Label("Open Projector", systemImage: "rectangle.on.rectangle")
            }
            .buttonStyle(.bordered)
            .accessibilityHint("Opens the audience window using the selected look")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .liveGlassSurface(cornerRadius: 18, interactive: true)
        .accessibilityElement(children: .contain)
    }

    private var bottomHUD: some View {
        HStack(spacing: 16) {
            statusBadge
            WaveformMeterView(levels: session.levels, rms: session.levels.last ?? 0)
            partialsIndicator

            Divider().frame(height: 22)

            HStack(spacing: 8) {
                Text("Aa")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Slider(value: $sessionManager.projectorFontSize, in: 36...144, step: 2)
                    .frame(width: 120)
            }

            Divider().frame(height: 22)

            Button(action: session.startStop) {
                Text(startStopTitle)
                    .font(.subheadline.weight(.semibold))
                    .frame(minWidth: 72)
            }
            .buttonStyle(.borderedProminent)
            .tint(session.status == .running ? .red : .accentColor)
            .disabled(session.status == .connecting)

            Button(session.paused ? "Resume" : "Pause", action: session.pauseResume)
                .buttonStyle(.bordered)
                .disabled(session.status != .running)

            Button {
                settingsPresented = true
            } label: {
                Label("Controls", systemImage: "slider.horizontal.3")
            }
            .buttonStyle(.bordered)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 12)
        .liveGlassSurface(cornerRadius: 24, interactive: true)
        .liveGlassGroup()
        .shadow(color: .black.opacity(0.35), radius: 18, y: 8)
    }

    private var statusBadge: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            Text(statusLabel)
                .font(.subheadline.weight(.semibold))
            Text("/")
                .foregroundStyle(.tertiary)
            Text("\(session.config.source_lang) to \(session.config.target_lang)")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(statusLabel), \(session.config.source_lang) to \(session.config.target_lang)")
    }

    private var partialsIndicator: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(Color.green)
                .frame(width: 9, height: 9)
                .opacity(partialPulseActive ? 1 : 0.25)
            Text("Partials")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
        }
    }

    private var sessionControlsSheet: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Session controls")
                    .font(.headline)
                Spacer()
                Button("Done") { settingsPresented = false }
                    .keyboardShortcut(.defaultAction)
            }
            .padding()

            Divider()

            ScrollView {
                OperatorSettingsPanel(
                    session: session,
                    sessionManager: sessionManager,
                    onOpenProjector: onOpenProjector
                )
            }
        }
        .frame(width: 620, height: 660)
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Text("Ready for live translation")
                .font(.title.weight(.semibold))
                .foregroundStyle(.white)
            Text("Start a session to fill the teleprompter.")
                .font(.title3)
                .foregroundStyle(.white.opacity(0.52))
        }
        .multilineTextAlignment(.center)
        .padding(.bottom, 80)
    }

    @ViewBuilder
    private func banner(text: String, tint: Color) -> some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(tint == .red ? Color.red.opacity(0.9) : Color.orange.opacity(0.95))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
            .background(tint.opacity(0.12))
    }

    private var cueAlignment: HorizontalAlignment {
        isRtlLanguage(session.config.target_lang) ? .trailing : .leading
    }

    private var textAlignment: TextAlignment {
        isRtlLanguage(session.config.target_lang) ? .trailing : .leading
    }

    private var frameAlignment: Alignment {
        isRtlLanguage(session.config.target_lang) ? .trailing : .leading
    }

    private var startStopTitle: String {
        switch session.status {
        case .running: "End"
        case .connecting: "Connecting"
        case .idle: "Start"
        }
    }

    private var statusLabel: String {
        switch session.status {
        case .running: "Live"
        case .connecting: "Connecting"
        case .idle: "Ready"
        }
    }

    private var statusColor: Color {
        switch session.status {
        case .running: .green
        case .connecting: .yellow
        case .idle: .gray
        }
    }

    private func scrollToLatest(using proxy: ScrollViewProxy) {
        guard let last = session.transcript.entries.last else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(last.id, anchor: .bottom)
        }
    }

    private func exportTXT() {
        TranscriptExporter.exportTXT(
            entries: session.transcript.entries,
            source: session.config.source_lang,
            target: session.config.target_lang
        )
    }
}

private struct TeleprompterShortcutMonitor: NSViewRepresentable {
    let onStartStop: () -> Void
    let onExportTXT: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        context.coordinator.start(
            onStartStop: onStartStop,
            onExportTXT: onExportTXT
        )
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        context.coordinator.updateHandlers(
            onStartStop: onStartStop,
            onExportTXT: onExportTXT
        )
    }

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.stop()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    final class Coordinator {
        private var monitor: Any?
        private var onStartStop: (() -> Void)?
        private var onExportTXT: (() -> Void)?

        func start(
            onStartStop: @escaping () -> Void,
            onExportTXT: @escaping () -> Void
        ) {
            updateHandlers(
                onStartStop: onStartStop,
                onExportTXT: onExportTXT
            )
            guard monitor == nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self else { return event }
                guard !self.isTypingTarget(event) else { return event }

                if event.keyCode == 49, event.modifierFlags.intersection(.deviceIndependentFlagsMask).isEmpty {
                    self.onStartStop?()
                    return nil
                }
                if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers?.lowercased() == "e" {
                    self.onExportTXT?()
                    return nil
                }
                return event
            }
        }

        func updateHandlers(
            onStartStop: @escaping () -> Void,
            onExportTXT: @escaping () -> Void
        ) {
            self.onStartStop = onStartStop
            self.onExportTXT = onExportTXT
        }

        func stop() {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
            monitor = nil
        }

        private func isTypingTarget(_ event: NSEvent) -> Bool {
            guard let responder = event.window?.firstResponder else { return false }
            return responder is NSTextView || responder is NSTextField
        }
    }
}
