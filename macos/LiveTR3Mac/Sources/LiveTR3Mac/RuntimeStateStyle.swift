import SwiftUI

extension LiveTR3Runtime.State {
    var label: String {
        switch self {
        case .idle: "Idle"
        case .starting: "Starting"
        case .ready: "Ready"
        case .failed: "Needs attention"
        }
    }

    var symbolName: String {
        switch self {
        case .idle: "pause.circle"
        case .starting: "bolt.horizontal.circle"
        case .ready: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .idle: .secondary
        case .starting: .blue
        case .ready: .green
        case .failed: .red
        }
    }
}
