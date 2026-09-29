import SwiftUI

struct OperatorHeaderView: View {
    @ObservedObject var session: SessionController
    @ObservedObject var sessionManager: SessionManager
    @Binding var settingsOpen: Bool
    let onOpenProjector: () -> Void

    @State private var partialPulseActive = false

    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            statusColumn
            Spacer(minLength: 12)
            waveformColumn
            Spacer(minLength: 12)
            controlsColumn
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
        .overlay(alignment: .bottom) {
            Divider()
        }
        .onChange(of: session.transcript.partialTickAt) { _, tick in
            guard tick != nil else { return }
            partialPulseActive = true
            Task {
                try? await Task.sleep(nanoseconds: 150_000_000)
                partialPulseActive = false
            }
        }
    }

    private var statusColumn: some View {
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
        .frame(minWidth: 220, alignment: .leading)
    }

    private var waveformColumn: some View {
        HStack(spacing: 12) {
            WaveformMeterView(levels: session.levels, rms: session.levels.last ?? 0)
            HStack(spacing: 6) {
                Circle()
                    .fill(Color.green)
                    .frame(width: 10, height: 10)
                    .opacity(partialPulseActive ? 1 : 0.2)
                Text("Partials")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .textCase(.uppercase)
            }
        }
    }

    private var controlsColumn: some View {
        HStack(spacing: 8) {
            Button(action: session.startStop) {
                Text(session.status == .running ? "End" : session.status == .connecting ? "Connecting" : "Start")
                    .font(.subheadline.weight(.semibold))
                    .frame(minWidth: 72)
            }
            .buttonStyle(.borderedProminent)
            .disabled(session.status == .connecting)

            Button(session.paused ? "Resume" : "Pause", action: session.pauseResume)
                .buttonStyle(.bordered)
                .disabled(session.status != .running)

            Button {
                settingsOpen.toggle()
            } label: {
                Label("Controls", systemImage: "slider.horizontal.3")
            }
            .buttonStyle(.bordered)
            .popover(isPresented: $settingsOpen, arrowEdge: .bottom) {
                OperatorSettingsPanel(
                    session: session,
                    sessionManager: sessionManager,
                    onOpenProjector: onOpenProjector
                )
                .frame(width: 560)
            }
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
}

struct OperatorSettingsPanel: View {
    @ObservedObject var session: SessionController
    @ObservedObject var sessionManager: SessionManager
    let onOpenProjector: () -> Void

    @State private var customVocabText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            languageSection
            inputSection
            outputSection
            sessionActionsSection
            exportSection
            customVocabSection
            advancedSection
        }
        .padding(16)
        .onAppear {
            customVocabText = session.config.custom_vocab.joined(separator: ", ")
        }
    }

    private var languageSection: some View {
        settingsSection(title: "Caption setup") {
            HStack(alignment: .bottom, spacing: 12) {
                languagePicker(title: "Source language", selection: sourceBinding)
                Button("Swap next", action: session.swapDirection)
                    .buttonStyle(.bordered)
                    .disabled(session.status != .running)
                languagePicker(title: "Target language", selection: targetBinding)
            }
        }
    }

    private var inputSection: some View {
        settingsSection(title: "Input") {
            HStack(alignment: .bottom, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Mic")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Picker("Mic", selection: $session.selectedDeviceID) {
                        Text("System default").tag("")
                        ForEach(session.devices) { device in
                            Text(device.name).tag(device.id)
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: 280)
                    .onChange(of: session.selectedDeviceID) { _, newValue in
                        session.setSelectedDeviceID(newValue)
                    }
                }

                Text("VAD: Silero")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                Toggle("Polish finals", isOn: polishBinding)
                    .font(.subheadline)
            }
        }
    }

    private var outputSection: some View {
        settingsSection(title: "Projector output") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .bottom, spacing: 12) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Projector font size")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        HStack {
                            Slider(value: $sessionManager.projectorFontSize, in: 36...144, step: 2)
                            Text("\(Int(sessionManager.projectorFontSize))px")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(width: 48, alignment: .trailing)
                        }
                    }
                    Button("Open Projector", action: onOpenProjector)
                        .buttonStyle(.borderedProminent)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Projector look")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    ProjectorLookPicker(selection: $sessionManager.projectorStyle)
                    Text(sessionManager.projectorStyle.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("The previous and current passages stay visible together. Each page gets at least 4 seconds before advancing, then stays for the next page’s reading time. Drafts settle before appearing.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var sessionActionsSection: some View {
        settingsSection(title: "Live session actions") {
            HStack(spacing: 8) {
                Button("Commit Now", action: session.commitNow)
                    .disabled(session.status != .running)
                Button("Skip Next Polish", action: session.skipNextPolish)
                    .disabled(session.status != .running || !session.config.polish_enabled)
                Button("Clear Transcript", action: session.clearTranscript)
            }
            .buttonStyle(.bordered)
        }
    }

    private var exportSection: some View {
        settingsSection(title: "Export") {
            HStack(spacing: 8) {
                Button("TXT") { exportTXT() }
                Button("SRT") { exportSRT() }
                Button("VTT") { exportVTT() }
            }
            .buttonStyle(.bordered)
        }
    }

    private var customVocabSection: some View {
        DisclosureGroup("Custom vocabulary") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Vocabulary hints")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                TextEditor(text: $customVocabText)
                    .font(.body)
                    .frame(minHeight: 72)
                    .onChange(of: customVocabText) { _, value in
                        var next = session.config
                        next.custom_vocab = value
                            .split(separator: ",")
                            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                            .filter { !$0.isEmpty }
                        session.updateConfig(next)
                    }
            }
            .padding(.top, 8)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(.secondary)
        .textCase(.uppercase)
    }

    private var advancedSection: some View {
        DisclosureGroup("Advanced timing") {
            VStack(alignment: .leading, spacing: 12) {
                Toggle("Code-switch aware prompting", isOn: codeSwitchBinding)
                HStack(spacing: 12) {
                    numberField(title: "Partial interval (s)", value: partialIntervalBinding, range: 0.3...3, step: 0.05)
                    numberField(title: "Max utterance (s)", value: maxUtteranceBinding, range: 5...29, step: 1)
                    numberField(title: "Silero threshold", value: sileroThresholdBinding, range: 0.1...0.95, step: 0.05)
                }
                HStack(spacing: 12) {
                    numberField(title: "Speech pad (ms)", value: speechPadBinding, range: 0...2_000, step: 50)
                    numberField(title: "Min silence (ms)", value: minSilenceBinding, range: 100...5_000, step: 50)
                }
            }
            .padding(.top, 8)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(.secondary)
        .textCase(.uppercase)
    }

    @ViewBuilder
    private func settingsSection<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            content()
        }
        .padding(.bottom, 4)
        .overlay(alignment: .bottom) {
            Divider()
        }
    }

    private func languagePicker(title: String, selection: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Picker(title, selection: selection) {
                ForEach(LiveTR3Language.allCases) { language in
                    Text(language.rawValue).tag(language.rawValue)
                }
            }
            .labelsHidden()
            .frame(maxWidth: 220)
        }
    }

    private func numberField(
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        step: Double
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            TextField(title, value: value, format: .number)
                .textFieldStyle(.roundedBorder)
                .onSubmit { session.updateConfig(session.config) }
        }
        .frame(maxWidth: 180)
    }

    private var sourceBinding: Binding<String> {
        Binding(
            get: { session.config.source_lang },
            set: { newValue in
                var next = session.config
                next.source_lang = newValue
                session.updateConfig(next)
            }
        )
    }

    private var targetBinding: Binding<String> {
        Binding(
            get: { session.config.target_lang },
            set: { newValue in
                var next = session.config
                next.target_lang = newValue
                session.updateConfig(next)
            }
        )
    }

    private var polishBinding: Binding<Bool> {
        Binding(
            get: { session.config.polish_enabled },
            set: { newValue in
                var next = session.config
                next.polish_enabled = newValue
                session.updateConfig(next)
            }
        )
    }

    private var codeSwitchBinding: Binding<Bool> {
        Binding(
            get: { session.config.code_switching_enabled ?? false },
            set: { newValue in
                var next = session.config
                next.code_switching_enabled = newValue
                session.updateConfig(next)
            }
        )
    }

    private var partialIntervalBinding: Binding<Double> {
        configDoubleBinding(keyPath: \.partial_interval_seconds, defaultValue: 0.75)
    }

    private var maxUtteranceBinding: Binding<Double> {
        configDoubleBinding(keyPath: \.max_utterance_seconds, defaultValue: 12)
    }

    private var sileroThresholdBinding: Binding<Double> {
        configDoubleBinding(keyPath: \.silero_threshold, defaultValue: 0.5)
    }

    private var speechPadBinding: Binding<Double> {
        configDoubleBinding(keyPath: \.speech_pad_ms, defaultValue: 300)
    }

    private var minSilenceBinding: Binding<Double> {
        configDoubleBinding(keyPath: \.min_silence_ms, defaultValue: 150)
    }

    private func configDoubleBinding(
        keyPath: WritableKeyPath<ClientConfig, Double?>,
        defaultValue: Double
    ) -> Binding<Double> {
        Binding(
            get: { session.config[keyPath: keyPath] ?? defaultValue },
            set: { newValue in
                var next = session.config
                next[keyPath: keyPath] = newValue
                session.updateConfig(next)
            }
        )
    }

    private func exportTXT() {
        TranscriptExporter.exportTXT(
            entries: session.transcript.entries,
            source: session.config.source_lang,
            target: session.config.target_lang
        )
    }

    private func exportSRT() {
        TranscriptExporter.exportSRT(
            entries: session.transcript.entries,
            source: session.config.source_lang,
            target: session.config.target_lang
        )
    }

    private func exportVTT() {
        TranscriptExporter.exportVTT(
            entries: session.transcript.entries,
            source: session.config.source_lang,
            target: session.config.target_lang
        )
    }
}
