import SwiftUI

struct TranscriptLineView: View {
    let entry: TranscriptUtterance
    let field: TranscriptField
    let layoutDirection: LayoutDirection

    enum TranscriptField {
        case original
        case translation

        func text(from entry: TranscriptUtterance) -> String {
            switch self {
            case .original: entry.original
            case .translation: entry.translation
            }
        }

        func stableLength(from entry: TranscriptUtterance) -> Int {
            switch self {
            case .original: entry.stableOriginalLength
            case .translation: entry.stableTranslationLength
            }
        }
    }

    private var text: String { field.text(from: entry) }
    private var stableLength: Int { field.stableLength(from: entry) }
    private var isPartial: Bool { entry.state == .partial }

    private var stableText: String {
        guard isPartial else { return text }
        return String(text.prefix(stableLength))
    }

    private var unstableText: String {
        guard isPartial else { return "" }
        return String(text.dropFirst(stableLength))
    }

    var body: some View {
        Text(attributedCaption)
            .font(.system(size: 30, weight: .regular))
            .lineSpacing(6)
            .frame(maxWidth: .infinity, alignment: layoutDirection == .rightToLeft ? .trailing : .leading)
            .environment(\.layoutDirection, layoutDirection)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
    }

    private var attributedCaption: AttributedString {
        var result = AttributedString(stableText)
        result.foregroundColor = isPartial ? Color.secondary : Color.primary
        result.font = .system(size: 30, weight: .regular).italic(isPartial)

        if !unstableText.isEmpty {
            var unstable = AttributedString(unstableText)
            unstable.foregroundColor = Color.secondary.opacity(0.7)
            unstable.font = .system(size: 30, weight: .regular).italic()
            result.append(unstable)
        }
        return result
    }
}

private extension Font {
    func italic(_ active: Bool) -> Font {
        active ? self.italic() : self
    }
}
