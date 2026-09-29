import Foundation
import Network

@MainActor
final class LocalEngineConnection: CaptionEngine {
    var onMessage: ((LiveTR3ServerMessage) -> Void)?
    var onDisconnect: (() -> Void)?

    private let socketPath: String
    private var connection: NWConnection?
    private var receiveTask: Task<Void, Never>?

    init(socketPath: String = LiveTR3Runtime.engineSocketPath.path) {
        self.socketPath = socketPath
    }

    func connect(sessionID: String, mode: CaptionEngineConnectionMode) async throws {
        disconnect()

        let connection = NWConnection(to: .unix(path: socketPath), using: .tcp)
        self.connection = connection

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let gate = ConnectionContinuationGate(continuation)
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    gate.resume()
                case .failed(let error):
                    gate.resume(throwing: error)
                default:
                    break
                }
            }
            connection.start(queue: .global(qos: .userInitiated))
        }

        startReceiveLoop()
        sendJSON(["type": "hello", "session": sessionID])

        switch mode {
        case .viewer:
            sendJSON(["type": "join_viewer"])
        case .resume:
            sendJSON(["type": "resume"])
        case .start:
            break
        }
    }

    func sendJSON(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else {
            return
        }
        sendFrame(type: .text, payload: data)
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
        sendFrame(type: .binary, payload: data)
    }

    func disconnect() {
        receiveTask?.cancel()
        receiveTask = nil
        connection?.cancel()
        connection = nil
    }

    private func sendFrame(type: LocalEngineFrameType, payload: Data) {
        guard let connection else { return }
        var framed = Data()
        framed.append(type.rawValue)
        framed.append(UInt8((payload.count >> 24) & 0xff))
        framed.append(UInt8((payload.count >> 16) & 0xff))
        framed.append(UInt8((payload.count >> 8) & 0xff))
        framed.append(UInt8(payload.count & 0xff))
        framed.append(payload)
        connection.send(content: framed, completion: .contentProcessed { _ in })
    }

    private func startReceiveLoop() {
        receiveTask?.cancel()
        receiveTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                do {
                    let header = try await self.receiveExact(length: 5)
                    let frameType = LocalEngineFrameType(rawValue: header[header.startIndex])
                    let length = Int(header[header.startIndex + 1]) << 24
                        | Int(header[header.startIndex + 2]) << 16
                        | Int(header[header.startIndex + 3]) << 8
                        | Int(header[header.startIndex + 4])
                    let payload = try await self.receiveExact(length: length)
                    if frameType == .text,
                       let parsed = LiveTR3ServerMessage.parse(payload) {
                        self.onMessage?(parsed)
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

    private func receiveExact(length: Int) async throws -> Data {
        guard length > 0 else { return Data() }
        guard let connection else {
            throw LiveTR3SessionError(message: "Local engine connection is closed.")
        }

        var result = Data()
        while result.count < length {
            let remaining = length - result.count
            let chunk = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Data, Error>) in
                connection.receive(minimumIncompleteLength: 1, maximumLength: remaining) { data, _, isComplete, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else if let data, !data.isEmpty {
                        continuation.resume(returning: data)
                    } else if isComplete {
                        continuation.resume(throwing: LiveTR3SessionError(message: "Local engine connection closed."))
                    } else {
                        continuation.resume(returning: Data())
                    }
                }
            }
            if chunk.isEmpty {
                throw LiveTR3SessionError(message: "Local engine connection closed.")
            }
            result.append(chunk)
        }
        return result
    }
}

private enum LocalEngineFrameType: UInt8 {
    case text = 0x01
    case binary = 0x02
}

private final class ConnectionContinuationGate: @unchecked Sendable {
    private let lock = NSLock()
    private var didResume = false
    private let continuation: CheckedContinuation<Void, Error>

    init(_ continuation: CheckedContinuation<Void, Error>) {
        self.continuation = continuation
    }

    func resume() {
        guard markResumed() else { return }
        continuation.resume()
    }

    func resume(throwing error: Error) {
        guard markResumed() else { return }
        continuation.resume(throwing: error)
    }

    private func markResumed() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if didResume {
            return false
        }
        didResume = true
        return true
    }
}
