import Foundation

@MainActor
final class TranscriptStore: ObservableObject {
    @Published private(set) var entries: [TranscriptUtterance] = []
    @Published private(set) var lastError: String?
    @Published private(set) var partialTickAt: Date?
    @Published private(set) var workerStatus: (state: WorkerState, message: String)?

    func handle(_ message: LiveTR3ServerMessage) {
        switch message {
        case .error(let message):
            lastError = message
        case .status(let state, let message):
            workerStatus = (state, message)
            if state == .ready {
                lastError = nil
            }
        case .speechStart(let utteranceID):
            guard !entries.contains(where: { $0.id == utteranceID }) else { return }
            partialTickAt = Date()
        case .level:
            break
        case .caption(let type, let utteranceID, let original, let translation):
            lastError = nil
            if type == .partial {
                partialTickAt = Date()
            }

            let existing = entries.first(where: { $0.id == utteranceID })
            // Delayed stream callbacks must never turn a completed caption back into a draft.
            if type == .partial, let existing, existing.state != .partial { return }
            if type == .final, existing?.state == .polished { return }
            let previousOriginal = existing?.original ?? ""
            let previousTranslation = existing?.translation ?? ""

            let updated = TranscriptUtterance(
                id: utteranceID,
                original: original,
                translation: translation,
                state: type,
                stableOriginalLength: type == .partial
                    ? longestCommonPrefixLength(previousOriginal, original)
                    : original.count,
                stableTranslationLength: type == .partial
                    ? longestCommonPrefixLength(previousTranslation, translation)
                    : translation.count,
                startedAt: existing?.startedAt ?? Date(),
                endedAt: type == .partial ? existing?.endedAt : Date()
            )

            if let index = entries.firstIndex(where: { $0.id == utteranceID }) {
                entries[index] = updated
            } else {
                entries.append(updated)
            }
        }
    }

    func clear() {
        entries = []
        lastError = nil
        workerStatus = nil
        partialTickAt = nil
    }
}
