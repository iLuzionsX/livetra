import Foundation

enum LiveTR3Section: String, CaseIterable, Identifiable {
    case operatorPanel
    case projector
    case runtime
    case archive

    static let primary: [LiveTR3Section] = [.operatorPanel, .projector]
    static let system: [LiveTR3Section] = [.runtime, .archive]

    var id: String { rawValue }

    var title: String {
        switch self {
        case .operatorPanel: "Operator"
        case .projector: "Projector"
        case .runtime: "Runtime"
        case .archive: "Archive"
        }
    }

    var subtitle: String {
        switch self {
        case .operatorPanel: "Live controls"
        case .projector: "Audience view"
        case .runtime: "Local services"
        case .archive: "Session history"
        }
    }

    var symbolName: String {
        switch self {
        case .operatorPanel: "waveform.and.mic"
        case .projector: "rectangle.on.rectangle"
        case .runtime: "bolt.horizontal"
        case .archive: "clock.arrow.circlepath"
        }
    }
}
