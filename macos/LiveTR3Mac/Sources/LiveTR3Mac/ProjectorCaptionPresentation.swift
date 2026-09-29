import AppKit
import Foundation

/// Audience timing is independent of inference and the operator's verbatim transcript.
/// A settled draft is still a hypothesis, never an accuracy/confidence score.
struct ProjectorCaptionPresentation {
    struct Caption: Equatable {
        var utteranceID: Int
        var original: String
        var translation: String
        var isContinuation = false
    }

    private struct Pending {
        var captions: [Caption]
        var originalOffset = 0
        var translationOffset = 0

        var caption: Caption {
            Caption(utteranceID: captions.last!.utteranceID,
                    original: captions.map(\.original).joined(separator: " "),
                    translation: captions.map(\.translation).joined(separator: " "))
        }
    }

    private(set) var current: Caption?
    private(set) var previous: Caption?
    private(set) var draft: Caption?
    private(set) var holdUntil: TimeInterval = 0
    private var active: Pending?
    private var pageStart: Pending?
    private var previousStart: Pending?
    private var queue: [Pending] = []
    private var finalIDs: Set<Int> = []
    private var draftID: Int?
    private var originalDraft = SettlingCaptionText()
    private var translationDraft = SettlingCaptionText()
    private var layout: ProjectorCaptionLayout?

    mutating func receive(_ entries: [TranscriptUtterance], at now: TimeInterval) {
        guard !entries.isEmpty else {
            self = Self()
            return
        }
        for entry in entries where entry.state != .partial {
            let caption = Caption(utteranceID: entry.id, original: entry.original, translation: entry.translation)
            if let old = previousStart?.captions.first(where: { $0.utteranceID == entry.id }), old != caption {
                // A correction must not leave an outdated claim in visible history.
                previous = nil
                previousStart = nil
            }
            // A missing translation must not advance the audience past a complete caption.
            guard !caption.translation.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
            if finalIDs.insert(entry.id).inserted {
                queue.append(Pending(captions: [caption]))
            } else if let index = active?.captions.firstIndex(where: { $0.utteranceID == entry.id }),
                      active?.captions[index] != caption {
                // A correction replaces the active utterance and earns fresh reading time.
                active?.captions[index] = caption
                active?.originalOffset = 0
                active?.translationOffset = 0
                if previous?.utteranceID == current?.utteranceID { previous = nil }
                current = nil
            } else if let index = queue.firstIndex(where: { $0.caption.utteranceID == entry.id }) {
                queue[index].captions = [caption]
            }
        }
        let newestFinalID = finalIDs.max() ?? Int.min
        if let entry = entries.last(where: { $0.state == .partial && $0.id > newestFinalID }) {
            if draftID != entry.id {
                originalDraft = SettlingCaptionText()
                translationDraft = SettlingCaptionText()
                draftID = entry.id
            }
            originalDraft.update(entry.original, at: now)
            translationDraft.update(entry.translation, at: now)
        } else {
            draftID = nil
            draft = nil
        }
    }

    mutating func tick(at now: TimeInterval, layout nextLayout: ProjectorCaptionLayout) {
        if layout != nextLayout {
            layout = nextLayout
            // Reflow the current page from its start, never from its already consumed end.
            if let pageStart, current != nil { active = pageStart }
            current = nil
        }
        if active == nil, !queue.isEmpty { takeNextPage(layout: nextLayout) }
        if current == nil, active != nil {
            showPage(at: now, layout: nextLayout)
        } else if now >= holdUntil {
            if hasRemainder {
                showPage(at: now, layout: nextLayout)
            } else if !queue.isEmpty {
                takeNextPage(layout: nextLayout)
                showPage(at: now, layout: nextLayout)
            }
            // The last page stays indefinitely during silence, even after its hold expires.
        }
        if let draftID, queue.isEmpty, !hasRemainder {
            let original = originalDraft.settled(at: now)
            let translation = translationDraft.settled(at: now)
            draft = original.isEmpty && translation.isEmpty ? nil : Caption(
                utteranceID: draftID,
                original: original,
                translation: translation
            )
        } else {
            draft = nil
        }
    }

    private var hasRemainder: Bool {
        guard let active, let layout else { return false }
        return (layout.style != .focus && active.originalOffset < active.caption.original.count)
            || active.translationOffset < active.caption.translation.count
    }

    private mutating func takeNextPage(layout: ProjectorCaptionLayout) {
        var next = queue.removeFirst()
        // Short utterances that arrive in a burst share the next page, instead of each
        // accumulating another four seconds of lag. Never change a page being read.
        while let candidate = queue.first {
            let combined = Pending(captions: next.captions + candidate.captions)
            let caption = combined.caption
            guard layout.prefix(caption.translation, source: false) == caption.translation,
                  layout.style == .focus || layout.prefix(caption.original, source: true) == caption.original else { break }
            next = combined
            queue.removeFirst()
        }
        active = next
    }

    private mutating func showPage(at now: TimeInterval, layout: ProjectorCaptionLayout) {
        guard var active else { return }
        if let current {
            previous = current
            previousStart = pageStart
        }
        pageStart = active
        let original = layout.style == .focus ? "" : layout.prefix(
            String(active.caption.original.dropFirst(active.originalOffset)), source: true
        )
        let translation = layout.prefix(
            String(active.caption.translation.dropFirst(active.translationOffset)), source: false
        )
        let page = Caption(
            utteranceID: active.caption.utteranceID,
            original: original,
            translation: translation,
            isContinuation: active.originalOffset > 0 || active.translationOffset > 0
        )
        active.originalOffset += original.count
        active.translationOffset += translation.count
        self.active = active
        current = page
        holdUntil = now + Self.readingDuration(original: original, translation: translation)
    }

    static func readingDuration(original: String, translation: String) -> TimeInterval {
        func duration(_ text: String) -> Double {
            let words = text.split(whereSeparator: \.isWhitespace).count
            // Character pacing also covers scripts that do not separate words with spaces.
            return max(Double(words) / 3, Double(text.count) / 15)
        }
        return max(4, max(duration(original), duration(translation)))
    }
}

private struct SettlingCaptionText {
    private var characters: [Character] = []
    private var firstSeen: [TimeInterval] = []

    mutating func update(_ text: String, at now: TimeInterval) {
        let next = Array(text)
        let common = zip(characters, next).prefix(while: { $0 == $1 }).count
        firstSeen = Array(firstSeen.prefix(common)) + Array(repeating: now, count: next.count - common)
        characters = next
    }

    func settled(at now: TimeInterval) -> String {
        let count = firstSeen.prefix(while: { now - $0 >= 0.7 }).count
        guard count > 0 else { return "" }
        // Withhold the changing word, even when only its first few letters have settled.
        var end = count
        while end > 0 {
            let character = characters[end - 1]
            if character.isWhitespace || ".!?。！？,;:،؛".contains(character) || isUnspaced(character) { break }
            if end < characters.count, characters[end].isWhitespace { break }
            end -= 1
        }
        return String(characters.prefix(end)).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func isUnspaced(_ character: Character) -> Bool {
        character.unicodeScalars.contains { scalar in
            (0x3040...0x30FF).contains(scalar.value) || (0x3400...0x9FFF).contains(scalar.value)
        }
    }
}

/// Fixed type, measured pages. Incoming text never causes the font to shrink.
struct ProjectorCaptionLayout: Equatable {
    let size: CGSize
    let requestedFontSize: CGFloat
    let style: ProjectorPresentationStyle

    var inset: CGFloat { max(32, size.width * 0.045) }
    var fontSize: CGFloat { min(requestedFontSize, max(36, size.height * (style == .stack ? 0.08 : 0.095))) }
    var sourceFontSize: CGFloat { style == .stack ? fontSize * 0.8 : fontSize }
    var lineSpacing: CGFloat { fontSize * 0.12 }
    var draftFontSize: CGFloat { max(28, fontSize * 0.65) }
    var draftHeight: CGFloat { draftFontSize * 1.5 + 44 }
    var mainHeight: CGFloat { max(120, size.height - inset * 2 - draftHeight - 24) }
    var columnWidth: CGFloat { style == .split ? (size.width - inset * 2 - 48) / 2 : size.width - inset * 2 }

    func textHeight(source: Bool) -> CGFloat {
        let available = style == .stack ? (mainHeight - 24) / 2 - 36 : mainHeight - 36
        let font = source ? sourceFontSize : fontSize
        // Reserve equal space for the previous and current page, at the same type size.
        return max(font * 1.3, min((available - 20) / 2, font * 2.5 + lineSpacing))
    }

    func prefix(_ text: String, source: Bool, balancePages: Bool = true) -> String {
        Self.fittingPrefix(text, width: columnWidth, height: textHeight(source: source),
                           fontSize: source ? sourceFontSize : fontSize, lineSpacing: lineSpacing,
                           balancePages: balancePages)
    }

    func draftExcerpt(_ text: String, width: CGFloat) -> String {
        // Drafts are explicitly previews; completed captions are paged in full above.
        let height = draftFontSize * 1.3
        if Self.height(text, width: width, fontSize: draftFontSize, lineSpacing: lineSpacing) <= height { return text }
        var characters = Array(text)
        while !characters.isEmpty {
            characters.removeFirst()
            if let first = characters.first, !first.isWhitespace { continue }
            let suffix = "… " + String(characters).trimmingCharacters(in: .whitespacesAndNewlines)
            if Self.height(suffix, width: width, fontSize: draftFontSize, lineSpacing: lineSpacing) <= height { return suffix }
        }
        // Long unspaced strings still get a visible preview rather than an empty lane.
        return Self.fittingPrefix(text, width: width, height: height, fontSize: draftFontSize, lineSpacing: lineSpacing)
    }

    static func fittingPrefix(_ text: String, width: CGFloat, height: CGFloat, fontSize: CGFloat,
                              lineSpacing: CGFloat, balancePages: Bool = true) -> String {
        let characters = Array(text)
        guard !characters.isEmpty else { return "" }
        var low = 1
        var high = characters.count
        while low < high {
            let mid = (low + high + 1) / 2
            if Self.height(String(characters.prefix(mid)), width: width, fontSize: fontSize, lineSpacing: lineSpacing) <= height {
                low = mid
            } else {
                high = mid - 1
            }
        }
        if low < characters.count {
            // Prefer a clause boundary and avoid leaving a single orphaned word on a page.
            let lowerBound = max(1, low / 2)
            if balancePages, let clause = characters[..<low].lastIndex(where: { ".!?。！？,;:،؛".contains($0) }), clause >= lowerBound {
                low = clause + 1
            } else if balancePages, characters.count - low < low / 4 {
                low = (characters.count + 1) / 2
            }
            // Prefer a word boundary, preserving every character for the following page.
            if !".!?。！？,;:،؛".contains(characters[low - 1]),
               let boundary = characters.prefix(low).lastIndex(where: \.isWhitespace), boundary > 0 {
                low = boundary + 1
            }
        }
        return String(characters.prefix(low))
    }

    static func height(_ text: String, width: CGFloat, fontSize: CGFloat, lineSpacing: CGFloat) -> CGFloat {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = lineSpacing
        paragraph.lineBreakMode = .byWordWrapping
        return (text as NSString).boundingRect(
            with: CGSize(width: max(1, width), height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: NSFont.systemFont(ofSize: fontSize, weight: .semibold), .paragraphStyle: paragraph]
        ).height.rounded(.up)
    }
}
