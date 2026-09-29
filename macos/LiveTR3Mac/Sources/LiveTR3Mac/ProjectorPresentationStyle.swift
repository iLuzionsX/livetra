import SwiftUI

enum ProjectorPresentationStyle: String, CaseIterable, Identifiable {
    case focus
    case split
    case stack

    var id: String { rawValue }

    var title: String {
        switch self {
        case .focus: "Focus"
        case .split: "Split"
        case .stack: "Stack"
        }
    }

    var detail: String {
        switch self {
        case .focus: "Large translation only"
        case .split: "Source and translation side by side"
        case .stack: "Source and translation stacked together"
        }
    }
}

struct ProjectorLookPicker: View {
    @Binding var selection: ProjectorPresentationStyle

    var body: some View {
        Picker("Projector look", selection: $selection) {
            ForEach(ProjectorPresentationStyle.allCases) { style in
                Text(style.title).tag(style)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .accessibilityLabel("Projector look")
        .accessibilityValue(selection.title)
        .accessibilityHint(selection.detail)
    }
}
