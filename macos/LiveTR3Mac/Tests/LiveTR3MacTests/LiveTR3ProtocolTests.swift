import Foundation
import XCTest
@testable import LiveTR3Mac

final class LiveTR3ProtocolTests: XCTestCase {
    func testDefaultClientConfigMatchesProtocolContract() {
        let config = ClientConfig.default
        XCTAssertEqual(config.version, 2)
        XCTAssertEqual(config.source_lang, "English")
        XCTAssertEqual(config.target_lang, "Spanish")
        XCTAssertEqual(config.segmenter, "silero")
        XCTAssertEqual(config.custom_vocab, [])
        XCTAssertEqual(config.polish_enabled, false)
        XCTAssertEqual(config.partial_interval_seconds, 0.25)
        XCTAssertEqual(config.max_utterance_seconds, 12)
    }

    func testCaptionMessageParses() throws {
        let data = try XCTUnwrap(
            #"{"type":"final","utterance_id":7,"original":"Hello","translation":"Hola"}"#
                .data(using: .utf8)
        )
        let message = try XCTUnwrap(LiveTR3ServerMessage.parse(data))

        guard case let .caption(type, utteranceID, original, translation) = message else {
            return XCTFail("Expected a caption message")
        }
        XCTAssertEqual(type, .final)
        XCTAssertEqual(utteranceID, 7)
        XCTAssertEqual(original, "Hello")
        XCTAssertEqual(translation, "Hola")
    }

    func testStatusAndErrorMessagesParse() throws {
        let statusData = try XCTUnwrap(
            #"{"type":"status","state":"recovering","message":"Reloading worker"}"#
                .data(using: .utf8)
        )
        let status = try XCTUnwrap(LiveTR3ServerMessage.parse(statusData))
        guard case let .status(state, message) = status else {
            return XCTFail("Expected a status message")
        }
        XCTAssertEqual(state, .recovering)
        XCTAssertEqual(message, "Reloading worker")

        let errorData = try XCTUnwrap(
            #"{"type":"error","message":"Model unavailable"}"#.data(using: .utf8)
        )
        let error = try XCTUnwrap(LiveTR3ServerMessage.parse(errorData))
        guard case let .error(message) = error else {
            return XCTFail("Expected an error message")
        }
        XCTAssertEqual(message, "Model unavailable")
    }

    func testMalformedAndUnknownMessagesAreRejected() throws {
        let missingID = try XCTUnwrap(
            #"{"type":"final","original":"Hello","translation":"Hola"}"#.data(using: .utf8)
        )
        XCTAssertNil(LiveTR3ServerMessage.parse(missingID))

        let unknown = try XCTUnwrap(#"{"type":"mystery"}"#.data(using: .utf8))
        XCTAssertNil(LiveTR3ServerMessage.parse(unknown))

        XCTAssertNil(LiveTR3ServerMessage.parse(Data("not json".utf8)))
    }

    func testLevelWithoutRMSUsesSafeZeroDefault() throws {
        let data = try XCTUnwrap(#"{"type":"level"}"#.data(using: .utf8))
        let message = try XCTUnwrap(LiveTR3ServerMessage.parse(data))
        guard case let .level(rms) = message else {
            return XCTFail("Expected a level message")
        }
        XCTAssertEqual(rms, 0)
    }

    func testLongestCommonPrefixLengthTracksStableCaptionText() {
        XCTAssertEqual(longestCommonPrefixLength("Hello wor", "Hello world"), 9)
        XCTAssertEqual(longestCommonPrefixLength("Hola", "Adios"), 0)
        XCTAssertEqual(longestCommonPrefixLength("こんにちは世", "こんにちは世界"), 6)
    }

    func testRTLLanguageDetectionIsNormalized() {
        XCTAssertTrue(isRtlLanguage(" Arabic "))
        XCTAssertTrue(isRtlLanguage("FARSI"))
        XCTAssertTrue(isRtlLanguage("Urdu"))
        XCTAssertFalse(isRtlLanguage("Spanish"))
    }
}

final class TranscriptStoreTests: XCTestCase {
    func testPartialUpdatesReuseUtteranceAndTrackStablePrefix() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(
                .caption(
                    type: .partial,
                    utteranceID: 11,
                    original: "Hello w",
                    translation: "Hola m"
                )
            )
            XCTAssertEqual(store.entries.count, 1)
            XCTAssertEqual(store.entries[0].stableOriginalLength, 0)
            XCTAssertEqual(store.entries[0].stableTranslationLength, 0)

            store.handle(
                .caption(
                    type: .partial,
                    utteranceID: 11,
                    original: "Hello world",
                    translation: "Hola mundo"
                )
            )

            XCTAssertEqual(store.entries.count, 1, "A partial update must not duplicate an utterance")
            XCTAssertEqual(store.entries[0].id, 11)
            XCTAssertEqual(store.entries[0].original, "Hello world")
            XCTAssertEqual(store.entries[0].translation, "Hola mundo")
            XCTAssertEqual(store.entries[0].stableOriginalLength, 7)
            XCTAssertEqual(store.entries[0].stableTranslationLength, 6)
            XCTAssertEqual(store.entries[0].state, .partial)
        }
    }

    func testFinalReplacesPartialWithoutDuplicate() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(
                .caption(type: .partial, utteranceID: 3, original: "Good", translation: "Buen")
            )
            store.handle(
                .caption(type: .final, utteranceID: 3, original: "Good morning", translation: "Buenos días")
            )

            XCTAssertEqual(store.entries.count, 1)
            let entry = store.entries[0]
            XCTAssertEqual(entry.state, .final)
            XCTAssertEqual(entry.original, "Good morning")
            XCTAssertEqual(entry.translation, "Buenos días")
            XCTAssertEqual(entry.stableOriginalLength, "Good morning".count)
            XCTAssertEqual(entry.stableTranslationLength, "Buenos días".count)
            XCTAssertNotNil(entry.endedAt)
        }
    }

    func testLatePartialCannotOverwriteFinalCaption() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(
                .caption(type: .partial, utteranceID: 8, original: "We can", translation: "Podemos")
            )
            store.handle(
                .caption(type: .final, utteranceID: 8, original: "We cannot", translation: "No podemos")
            )
            store.handle(
                .caption(type: .partial, utteranceID: 8, original: "We can", translation: "Podemos")
            )

            XCTAssertEqual(store.entries.count, 1)
            XCTAssertEqual(store.entries[0].state, .final)
            XCTAssertEqual(store.entries[0].original, "We cannot")
            XCTAssertEqual(store.entries[0].translation, "No podemos")
        }
    }

    func testReadyStatusClearsPriorError() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(.error(message: "worker failed"))
            XCTAssertEqual(store.lastError, "worker failed")

            store.handle(.status(state: .recovering, message: "reloading"))
            XCTAssertEqual(store.lastError, "worker failed")
            XCTAssertEqual(store.workerStatus?.state, .recovering)

            store.handle(.status(state: .ready, message: "ready"))
            XCTAssertNil(store.lastError)
            XCTAssertEqual(store.workerStatus?.state, .ready)
        }
    }

    func testClearResetsSessionVisibleState() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(.caption(type: .final, utteranceID: 1, original: "Hello", translation: "Hola"))
            store.handle(.error(message: "error"))
            store.handle(.status(state: .failed, message: "failed"))

            store.clear()

            XCTAssertTrue(store.entries.isEmpty)
            XCTAssertNil(store.lastError)
            XCTAssertNil(store.workerStatus)
            XCTAssertNil(store.partialTickAt)
        }
    }
}
