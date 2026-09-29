import Foundation

enum WebSocketConnectionMode {
    case start
    case resume
    case viewer
}

@MainActor
final class LiveTR3WebSocket: NSObject, CaptionEngine {
    var onMessage: ((LiveTR3ServerMessage) -> Void)?
    var onDisconnect: (() -> Void)?

    private var task: URLSessionWebSocketTask?
    private let session = URLSession(configuration: .default)
    private var receiveTask: Task<Void, Never>?

    func connect(sessionID: String, mode: CaptionEngineConnectionMode) async throws {
        disconnect()

        var components = URLComponents()
        components.scheme = "ws"
        components.host = "127.0.0.1"
        components.port = 8765
        components.path = "/"
        components.queryItems = [URLQueryItem(name: "session", value: sessionID)]

        guard let url = components.url else {
            throw LiveTR3SessionError(message: "Invalid WebSocket URL")
        }

        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()

        startReceiveLoop()

        switch mode {
        case .viewer:
            sendJSON(["type": "join_viewer"])
        case .resume:
            sendJSON(["type": "resume"])
            fallthrough
        case .start:
            break
        }
    }

    func sendJSON(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else {
            return
        }
        task?.send(.string(text)) { _ in }
    }

    func sendConfig(_ config: ClientConfig) {
        guard let data = try? JSONEncoder().encode(config),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        var payload = object
        payload["type"] = "config"
        sendJSON(payload)
    }

    func sendBinary(_ data: Data) {
        task?.send(.data(data)) { _ in }
    }

    func disconnect() {
        receiveTask?.cancel()
        receiveTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    private func startReceiveLoop() {
        receiveTask?.cancel()
        receiveTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                guard let task = self.task else { return }
                do {
                    let message = try await task.receive()
                    switch message {
                    case .string(let text):
                        if let data = text.data(using: .utf8),
                           let parsed = LiveTR3ServerMessage.parse(data) {
                            self.onMessage?(parsed)
                        }
                    case .data(let data):
                        if let parsed = LiveTR3ServerMessage.parse(data) {
                            self.onMessage?(parsed)
                        }
                    @unknown default:
                        break
                    }
                } catch {
                    if !Task.isCancelled {
                        self.onDisconnect?()
                    }
                    return
                }
            }
        }
    }
}

struct LiveTR3SessionError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
