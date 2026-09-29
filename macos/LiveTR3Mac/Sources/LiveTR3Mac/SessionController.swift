import AVFoundation
import Combine
import Foundation

enum SessionCaptureStatus: Equatable {
    case idle
    case connecting
    case running
}

@MainActor
final class SessionController: ObservableObject {
    @Published private(set) var status: SessionCaptureStatus = .idle
    @Published private(set) var paused = false
    @Published private(set) var error: String?
    @Published private(set) var levels: [Float] = Array(repeating: 0, count: 10)
    @Published var config: ClientConfig
    @Published var selectedDeviceID: String = ""

    let transcript = TranscriptStore()

    private let sessionManager: SessionManager
    private let runtime: LiveTR3Runtime
    private let engine: CaptionEngine
    private let audio = AudioCaptureEngine()

    private var manualStop = false
    private var reconnectAttempt = 0
    private var reconnectTask: Task<Void, Never>?

    init(sessionManager: SessionManager, runtime: LiveTR3Runtime) {
        self.sessionManager = sessionManager
        self.runtime = runtime
        self.engine = Self.makeEngine()
        self.config = Self.loadConfig()

        engine.onMessage = { [weak self] message in
            Task { @MainActor in
                self?.handleServerMessage(message)
            }
        }
        engine.onDisconnect = { [weak self] in
            Task { @MainActor in
                self?.handleDisconnect()
            }
        }
    }

    var devices: [AudioInputDevice] {
        AudioCaptureEngine.listInputDevices()
    }

    var sessionID: String {
        sessionManager.sessionID
    }

    func refreshDevices() {
        objectWillChange.send()
    }

    func startStop() {
        switch status {
        case .running, .connecting:
            stop()
        case .idle:
            Task { await start() }
        }
    }

    func pauseResume() {
        guard status == .running else { return }
        paused.toggle()
        audio.setPaused(paused)
    }

    func commitNow() {
        engine.sendJSON(["type": "commit_now"])
    }

    func skipNextPolish() {
        engine.sendJSON(["type": "skip_polish"])
    }

    func swapDirection() {
        config = ClientConfig(
            version: 2,
            source_lang: config.target_lang,
            target_lang: config.source_lang,
            custom_vocab: config.custom_vocab,
            segmenter: config.segmenter,
            polish_enabled: config.polish_enabled,
            apply_target: "next_utterance",
            input_device_id: selectedDeviceID.nilIfEmpty,
            input_device_label: selectedDeviceLabel,
            code_switching_enabled: config.code_switching_enabled,
            partial_interval_seconds: config.partial_interval_seconds,
            max_utterance_seconds: config.max_utterance_seconds,
            silero_threshold: config.silero_threshold,
            speech_pad_ms: config.speech_pad_ms,
            min_silence_ms: config.min_silence_ms,
            early_commit_enabled: config.early_commit_enabled,
            early_commit_min_seconds: config.early_commit_min_seconds,
            early_commit_punctuation: config.early_commit_punctuation,
            early_commit_stability: config.early_commit_stability,
            stability_window: config.stability_window
        )
        persistConfig()
        if status == .running {
            sendConfig(applyTarget: "next_utterance")
        }
    }

    func updateConfig(_ next: ClientConfig, applyTarget: String = "immediate") {
        config = next
        persistConfig()
        if status == .running {
            sendConfig(applyTarget: applyTarget)
        }
    }

    func setSelectedDeviceID(_ deviceID: String) {
        selectedDeviceID = deviceID
        if status == .running {
            Task {
                do {
                    try audio.switchDevice(deviceID.nilIfEmpty)
                    error = nil
                } catch {
                    self.error = error.localizedDescription
                }
            }
        }
    }

    func clearTranscript() {
        transcript.clear()
    }

    private func start() async {
        guard status == .idle else { return }
        manualStop = false
        error = nil
        status = .connecting

        do {
            try await requestMicrophoneAccess()
            try await ensureRuntimeReady()
            try await connectEngine(mode: .start)
            try audio.start(
                deviceID: selectedDeviceID.nilIfEmpty,
                onFrame: { [weak self] data in
                    Task { @MainActor in
                        self?.engine.sendBinary(data)
                    }
                },
                onLevel: { [weak self] rms in
                    Task { @MainActor in
                        self?.appendLevel(rms)
                    }
                }
            )
            sendConfig(applyTarget: "immediate")
            engine.sendJSON(["type": "start"])
            reconnectAttempt = 0
            status = .running
            paused = false
            error = nil
        } catch {
            stop()
            self.error = error.localizedDescription
        }
    }

    private func stop() {
        manualStop = true
        reconnectTask?.cancel()
        reconnectTask = nil
        engine.sendJSON(["type": "stop"])
        engine.disconnect()
        audio.stop()
        paused = false
        status = .idle
    }

    private func connectEngine(mode: CaptionEngineConnectionMode) async throws {
        try await engine.connect(sessionID: sessionManager.sessionID, mode: mode)
        if mode == .resume {
            sendConfig(applyTarget: "immediate")
        }
    }

    private func sendConfig(applyTarget: String) {
        var live = config
        live.version = 2
        live.apply_target = applyTarget
        live.input_device_id = selectedDeviceID.nilIfEmpty
        live.input_device_label = selectedDeviceLabel
        engine.sendConfig(live)
    }

    private func handleServerMessage(_ message: LiveTR3ServerMessage) {
        if case .level(let rms) = message {
            appendLevel(rms)
        }
        transcript.handle(message)
        if case .error(let message) = message {
            error = message
        }
    }

    private func handleDisconnect() {
        guard !manualStop else {
            status = .idle
            return
        }
        status = .connecting
        error = "Local engine connection interrupted. Reconnecting to local engine..."
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        reconnectTask?.cancel()
        let delay = min(1_000 * Int(pow(2.0, Double(reconnectAttempt))), 8_000)
        reconnectAttempt += 1
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay) * 1_000_000)
            guard let self, !Task.isCancelled, !self.manualStop else { return }
            do {
                try await self.connectEngine(mode: .resume)
                self.reconnectAttempt = 0
                self.status = .running
                self.error = nil
            } catch {
                self.error = error.localizedDescription
                self.scheduleReconnect()
            }
        }
    }

    private func appendLevel(_ rms: Float) {
        if levels.count >= 10 {
            levels.removeFirst()
        }
        levels.append(rms)
    }

    private var selectedDeviceLabel: String? {
        devices.first(where: { $0.id == selectedDeviceID })?.name
    }

    private func requestMicrophoneAccess() async throws {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return
        case .notDetermined:
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            guard granted else {
                throw LiveTR3SessionError(message: "Microphone access was denied.")
            }
        default:
            throw LiveTR3SessionError(message: "Microphone access is blocked. Enable it in System Settings.")
        }
    }

    private func ensureRuntimeReady() async throws {
        switch runtime.state {
        case .ready:
            return
        case .idle, .failed:
            await runtime.start()
        case .starting:
            break
        }

        for _ in 0..<80 {
            if runtime.state == .ready {
                return
            }
            if runtime.state == .failed {
                throw LiveTR3SessionError(message: runtime.statusMessage)
            }
            try await Task.sleep(nanoseconds: 250_000_000)
        }

        throw LiveTR3SessionError(message: "Local engine did not become ready.")
    }

    private static func makeEngine() -> CaptionEngine {
        if ProcessInfo.processInfo.environment["LIVETR3_DEBUG_WEBSOCKET"] == "1" {
            return LiveTR3WebSocket()
        }
        return LocalEngineConnection()
    }

    private func persistConfig() {
        if let data = try? JSONEncoder().encode(config) {
            UserDefaults.standard.set(data, forKey: "LiveTR3.clientConfig")
        }
    }

    private static func loadConfig() -> ClientConfig {
        guard let data = UserDefaults.standard.data(forKey: "LiveTR3.clientConfig"),
              let config = try? JSONDecoder().decode(ClientConfig.self, from: data) else {
            return .default
        }
        return optimizedConfig(config)
    }

    private static func optimizedConfig(_ config: ClientConfig) -> ClientConfig {
        var next = config
        if next.partial_interval_seconds == nil
            || next.partial_interval_seconds == 0.75
            || next.partial_interval_seconds == 0.45 {
            next.partial_interval_seconds = 0.25
        }
        next.early_commit_enabled = false
        if next.early_commit_min_seconds == nil {
            next.early_commit_min_seconds = 1.0
        }
        if next.early_commit_punctuation == nil {
            next.early_commit_punctuation = true
        }
        if next.early_commit_stability == nil {
            next.early_commit_stability = true
        }
        if next.stability_window == nil {
            next.stability_window = 2
        }
        return next
    }
}

@MainActor
final class ProjectorConnection: ObservableObject {
    @Published private(set) var connectionError: String?
    let transcript = TranscriptStore()

    private let sessionID: String
    private let engine: CaptionEngine
    private var transcriptObservation: AnyCancellable? = nil

    init(sessionID: String) {
        self.sessionID = sessionID
        self.engine = ProcessInfo.processInfo.environment["LIVETR3_DEBUG_WEBSOCKET"] == "1"
            ? LiveTR3WebSocket()
            : LocalEngineConnection()
        self.transcriptObservation = transcript.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
        engine.onMessage = { [weak self] message in
            Task { @MainActor in
                self?.transcript.handle(message)
                if case .error(let message) = message {
                    self?.connectionError = message
                }
            }
        }
        engine.onDisconnect = { [weak self] in
            Task { @MainActor in
                self?.connectionError = self?.connectionError ?? "Projector local engine connection closed"
            }
        }
    }

    func connect() {
        Task {
            do {
                try await engine.connect(sessionID: sessionID, mode: .viewer)
                connectionError = nil
            } catch {
                connectionError = "Projector local engine connection failed"
            }
        }
    }

    func disconnect() {
        engine.disconnect()
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}
