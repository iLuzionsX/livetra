import Foundation

enum LiveTR3Language: String, CaseIterable, Identifiable {
    case english = "English"
    case spanish = "Spanish"
    case french = "French"
    case german = "German"
    case italian = "Italian"
    case portuguese = "Portuguese"
    case japanese = "Japanese"
    case korean = "Korean"
    case mandarin = "Mandarin"
    case arabic = "Arabic"

    var id: String { rawValue }
}

struct ClientConfig: Codable, Equatable {
    var version: Int = 2
    var source_lang: String
    var target_lang: String
    var custom_vocab: [String]
    var segmenter: String = "silero"
    var polish_enabled: Bool
    var apply_target: String?
    var input_device_id: String?
    var input_device_label: String?
    var code_switching_enabled: Bool?
    var partial_interval_seconds: Double?
    var max_utterance_seconds: Double?
    var silero_threshold: Double?
    var speech_pad_ms: Double?
    var min_silence_ms: Double?
    var early_commit_enabled: Bool?
    var early_commit_min_seconds: Double?
    var early_commit_punctuation: Bool?
    var early_commit_stability: Bool?
    var stability_window: Int?

    static let `default` = ClientConfig(
        source_lang: LiveTR3Language.english.rawValue,
        target_lang: LiveTR3Language.spanish.rawValue,
        custom_vocab: [],
        polish_enabled: false,
        code_switching_enabled: false,
        partial_interval_seconds: 0.25,
        max_utterance_seconds: 12,
        silero_threshold: 0.5,
        speech_pad_ms: 300,
        min_silence_ms: 150,
        early_commit_enabled: false,
        early_commit_min_seconds: 1.0,
        early_commit_punctuation: true,
        early_commit_stability: true,
        stability_window: 2
    )
}

enum UtteranceState: String, Codable {
    case partial
    case final
    case polished
}

struct TranscriptUtterance: Identifiable, Equatable {
    let id: Int
    var original: String
    var translation: String
    var state: UtteranceState
    var stableOriginalLength: Int
    var stableTranslationLength: Int
    var startedAt: Date
    var endedAt: Date?
}

enum WorkerState: String, Codable {
    case starting
    case ready
    case recovering
    case failed
}

enum LiveTR3ServerMessage {
    case speechStart(utteranceID: Int)
    case caption(type: UtteranceState, utteranceID: Int, original: String, translation: String)
    case level(rms: Float)
    case status(state: WorkerState, message: String)
    case error(message: String)

    static func parse(_ data: Data) -> LiveTR3ServerMessage? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return nil
        }

        switch type {
        case "speech_start":
            guard let id = json["utterance_id"] as? Int else { return nil }
            return .speechStart(utteranceID: id)
        case "partial", "final", "polished":
            guard let id = json["utterance_id"] as? Int,
                  let original = json["original"] as? String,
                  let translation = json["translation"] as? String,
                  let state = UtteranceState(rawValue: type) else {
                return nil
            }
            return .caption(type: state, utteranceID: id, original: original, translation: translation)
        case "level":
            let rms = (json["rms"] as? NSNumber)?.floatValue ?? 0
            return .level(rms: rms)
        case "status":
            guard let stateRaw = json["state"] as? String,
                  let state = WorkerState(rawValue: stateRaw),
                  let message = json["message"] as? String else {
                return nil
            }
            return .status(state: state, message: message)
        case "error":
            guard let message = json["message"] as? String else { return nil }
            return .error(message: message)
        default:
            return nil
        }
    }
}

func longestCommonPrefixLength(_ a: String, _ b: String) -> Int {
    let aChars = Array(a)
    let bChars = Array(b)
    let maxCount = min(aChars.count, bChars.count)
    var index = 0
    while index < maxCount, aChars[index] == bChars[index] {
        index += 1
    }
    return index
}

func isRtlLanguage(_ language: String) -> Bool {
    let normalized = language.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return ["arabic", "hebrew", "urdu", "persian", "farsi"].contains(normalized)
}
