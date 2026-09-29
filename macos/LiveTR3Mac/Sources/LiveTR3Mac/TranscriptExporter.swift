import AppKit
import Foundation

enum TranscriptExporter {
    static func exportTXT(entries: [TranscriptUtterance], source: String, target: String) {
        let body = committed(entries)
            .map { "\(source): \($0.original)\n\(target): \($0.translation)" }
            .joined(separator: "\n\n")
        savePanel(filename: "livetr3-transcript.txt", contents: body + "\n")
    }

    static func exportSRT(entries: [TranscriptUtterance], source: String, target: String) {
        let committedEntries = committed(entries)
        guard let anchor = committedEntries.first?.startedAt else { return }
        let body = committedEntries.enumerated().map { index, entry in
            let start = max(0, entry.startedAt.timeIntervalSince(anchor))
            let end = max(start + 1, (entry.endedAt ?? Date()).timeIntervalSince(anchor))
            return """
            \(index + 1)
            \(formatSRTTime(start)) --> \(formatSRTTime(end))
            \(source): \(entry.original)
            \(target): \(entry.translation)
            """
        }.joined(separator: "\n\n")
        savePanel(filename: "livetr3-transcript.srt", contents: body + "\n")
    }

    static func exportVTT(entries: [TranscriptUtterance], source: String, target: String) {
        let committedEntries = committed(entries)
        guard let anchor = committedEntries.first?.startedAt else { return }
        let cues = committedEntries.map { entry in
            let start = max(0, entry.startedAt.timeIntervalSince(anchor))
            let end = max(start + 1, (entry.endedAt ?? Date()).timeIntervalSince(anchor))
            return """
            \(formatVTTTime(start)) --> \(formatVTTTime(end))
            \(source): \(entry.original)
            \(target): \(entry.translation)
            """
        }.joined(separator: "\n\n")
        savePanel(filename: "livetr3-transcript.vtt", contents: "WEBVTT\n\n\(cues)\n")
    }

    private static func committed(_ entries: [TranscriptUtterance]) -> [TranscriptUtterance] {
        entries.filter { $0.state != .partial && !$0.original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    private static func savePanel(filename: String, contents: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = filename
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        try? contents.write(to: url, atomically: true, encoding: .utf8)
    }

    private static func formatSRTTime(_ seconds: TimeInterval) -> String {
        let totalMilliseconds = Int(seconds * 1_000)
        let milliseconds = totalMilliseconds % 1_000
        let totalSeconds = totalMilliseconds / 1_000
        let secondsPart = totalSeconds % 60
        let minutes = (totalSeconds / 60) % 60
        let hours = totalSeconds / 3_600
        return String(format: "%02d:%02d:%02d,%03d", hours, minutes, secondsPart, milliseconds)
    }

    private static func formatVTTTime(_ seconds: TimeInterval) -> String {
        formatSRTTime(seconds).replacingOccurrences(of: ",", with: ".")
    }
}
