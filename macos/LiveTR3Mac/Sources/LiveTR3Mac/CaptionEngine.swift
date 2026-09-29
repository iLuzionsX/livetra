import Foundation

enum CaptionEngineConnectionMode {
    case start
    case resume
    case viewer
}

@MainActor
protocol CaptionEngine: AnyObject {
    var onMessage: ((LiveTR3ServerMessage) -> Void)? { get set }
    var onDisconnect: (() -> Void)? { get set }

    func connect(sessionID: String, mode: CaptionEngineConnectionMode) async throws
    func sendJSON(_ payload: [String: Any])
    func sendConfig(_ config: ClientConfig)
    func sendBinary(_ data: Data)
    func disconnect()
}
