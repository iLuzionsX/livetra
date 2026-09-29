import AppKit
import SwiftUI
import XCTest
@testable import LiveTR3Mac

final class ProjectorCaptionPresentationTests: XCTestCase {
    private let layout = ProjectorCaptionLayout(size: CGSize(width: 1280, height: 720), requestedFontSize: 72, style: .split)

    private func entry(_ id: Int, _ original: String, _ translation: String, state: UtteranceState = .final) -> TranscriptUtterance {
        TranscriptUtterance(id: id, original: original, translation: translation, state: state,
                            stableOriginalLength: 0, stableTranslationLength: 0,
                            startedAt: Date(timeIntervalSince1970: 0), endedAt: nil)
    }

    func testRapidFinalsWaitTheirTurnAndLastCaptionRemainsDuringSilence() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "Welcome.", "Bienvenidos.")
        let second = entry(2, "Please sit down.", "Por favor, siéntense.")
        model.receive([first], at: 0)
        model.tick(at: 0, layout: layout)
        model.receive([first, second], at: 1)
        model.tick(at: 3.9, layout: layout)
        XCTAssertEqual(model.current?.utteranceID, 1)
        model.tick(at: 4, layout: layout)
        XCTAssertEqual(model.current?.utteranceID, 2)
        model.tick(at: 400, layout: layout)
        XCTAssertEqual(model.current?.translation, second.translation)
    }

    func testDraftGrowthDoesNotResetOlderWordsAndIncompleteWordIsWithheld() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "There are nin", "Hay nove", state: .partial)
        model.receive([first], at: 0)
        model.tick(at: 0.5, layout: layout)
        XCTAssertNil(model.draft)
        model.receive([entry(1, "There are ninety liters ", "Hay noventa litros ", state: .partial)], at: 0.5)
        model.tick(at: 0.8, layout: layout)
        XCTAssertEqual(model.draft?.original, "There are")
        XCTAssertEqual(model.draft?.translation, "Hay")
        model.tick(at: 1.3, layout: layout)
        XCTAssertEqual(model.draft?.original, "There are ninety liters")
        XCTAssertNil(model.current, "A settled draft must never be promoted to a final")
    }

    func testCorrectionRetractsWrongDraftAndFinalIsAuthoritative() {
        var model = ProjectorCaptionPresentation()
        model.receive([entry(1, "There are nineteen liters ", "Hay diecinueve litros ", state: .partial)], at: 0)
        model.tick(at: 1, layout: layout)
        XCTAssertTrue(model.draft?.original.contains("nineteen") == true)
        model.receive([entry(1, "There are ninety liters ", "Hay noventa litros ", state: .partial)], at: 1.1)
        model.tick(at: 1.1, layout: layout)
        XCTAssertEqual(model.draft?.original, "There are")
        model.receive([entry(1, "There are ninety liters.", "Hay noventa litros.")], at: 1.2)
        model.tick(at: 1.2, layout: layout)
        XCTAssertEqual(model.current?.original, "There are ninety liters.")
        XCTAssertNil(model.draft)
        XCTAssertEqual(model.holdUntil, 5.2, accuracy: 0.001)
    }

    func testNewSpeechNeverEvictsFinishedCaption() {
        var model = ProjectorCaptionPresentation()
        let final = entry(1, "Welcome.", "Bienvenidos.")
        model.receive([final], at: 0)
        model.tick(at: 0, layout: layout)
        model.receive([final, entry(2, "Next words ", "Otras palabras ", state: .partial)], at: 1)
        model.tick(at: 2, layout: layout)
        XCTAssertEqual(model.current?.utteranceID, 1)
        XCTAssertEqual(model.draft?.utteranceID, 2)
        model.tick(at: 200, layout: layout)
        XCTAssertEqual(model.current?.utteranceID, 1)
    }

    func testLongFinalPagesPreserveEveryCharacterAcrossLanguagesAndStyles() {
        let original = String(repeating: "We have ninety liters, not nineteen. ", count: 8)
        let translations = [String(repeating: "Tenemos noventa litros, no diecinueve. ", count: 10),
                            String(repeating: "これは長い文章です。数量は九十です。", count: 14),
                            String(repeating: "لدينا تسعون لترًا وليس تسعة عشر. ", count: 10)]
        for style in ProjectorPresentationStyle.allCases {
            for translation in translations {
                let layout = ProjectorCaptionLayout(size: CGSize(width: 1280, height: 720), requestedFontSize: 72, style: style)
                var model = ProjectorCaptionPresentation()
                model.receive([entry(1, original, translation)], at: 0)
                var seenOriginal = ""
                var seenTranslation = ""
                var now = 0.0
                for _ in 0..<100 {
                    model.tick(at: now, layout: layout)
                    let page = model.current!
                    seenOriginal += page.original
                    seenTranslation += page.translation
                    for (text, source) in [(page.original, true), (page.translation, false)] where !text.isEmpty {
                        XCTAssertLessThanOrEqual(
                            ProjectorCaptionLayout.height(text, width: layout.columnWidth,
                                fontSize: source ? layout.sourceFontSize : layout.fontSize, lineSpacing: layout.lineSpacing),
                            layout.textHeight(source: source), "Page must fit without shrinking or clipping")
                    }
                    if seenTranslation.count == translation.count && (style == .focus || seenOriginal.count == original.count) { break }
                    now = model.holdUntil
                }
                XCTAssertEqual(seenTranslation, translation)
                XCTAssertEqual(seenOriginal, style == .focus ? "" : original)
            }
        }
    }

    func testResizeReflowsFromCurrentPageStartWithoutLosingText() {
        var model = ProjectorCaptionPresentation()
        let text = String(repeating: "A longer sentence with more words. ", count: 6)
        model.receive([entry(1, text, text)], at: 0)
        model.tick(at: 0, layout: layout)
        let smaller = ProjectorCaptionLayout(size: CGSize(width: 1280, height: 720), requestedFontSize: 110, style: .stack)
        model.tick(at: 1, layout: smaller)
        XCTAssertTrue(text.hasPrefix(model.current!.original))
        XCTAssertTrue(text.hasPrefix(model.current!.translation))
        XCTAssertGreaterThanOrEqual(model.holdUntil, 5)
    }

    func testPolishedCorrectionUpdatesCurrentButDoesNotReplayAnOldUtterance() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "Nineteen.", "Diecinueve.")
        model.receive([first], at: 0)
        model.tick(at: 0, layout: layout)
        let corrected = entry(1, "Ninety.", "Noventa.", state: .polished)
        model.receive([corrected], at: 3)
        model.tick(at: 3, layout: layout)
        XCTAssertEqual(model.current?.translation, "Noventa.")
        XCTAssertEqual(model.holdUntil, 7)
        let second = entry(2, "Thanks.", "Gracias.")
        model.receive([corrected, second], at: 4)
        model.tick(at: 7, layout: layout)
        model.receive([entry(1, "Ninety!", "¡Noventa!", state: .polished), second], at: 8)
        model.tick(at: 50, layout: layout)
        XCTAssertEqual(model.current?.utteranceID, 2)
    }

    func testClearResetsPendingCaptionsDraftsAndReusedIDs() {
        var model = ProjectorCaptionPresentation()
        model.receive([entry(1, "One", "Uno"), entry(2, "Two", "Dos")], at: 0)
        model.tick(at: 0, layout: layout)
        model.receive([], at: 1)
        model.tick(at: 10, layout: layout)
        XCTAssertNil(model.current)
        XCTAssertNil(model.draft)
        model.receive([entry(1, "New", "Nuevo")], at: 11)
        model.tick(at: 11, layout: layout)
        XCTAssertEqual(model.current?.translation, "Nuevo")
    }

    func testReadingTimeGrowsWithTextAndHandlesUnspacedScripts() {
        XCTAssertEqual(ProjectorCaptionPresentation.readingDuration(original: "", translation: "Hello."), 4)
        XCTAssertGreaterThan(ProjectorCaptionPresentation.readingDuration(original: "", translation: String(repeating: "word ", count: 30)), 9)
        XCTAssertGreaterThan(ProjectorCaptionPresentation.readingDuration(original: "", translation: String(repeating: "文", count: 90)), 5)
    }

    func testBurstOfShortFinalsSharesNextPageWithoutChangingPageBeingRead() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "Welcome.", "Bienvenidos.")
        model.receive([first], at: 0)
        model.tick(at: 0, layout: layout)
        let burst = [first, entry(2, "One.", "Uno."), entry(3, "Two.", "Dos."), entry(4, "Three.", "Tres.")]
        model.receive(burst, at: 1)
        model.tick(at: 1, layout: layout)
        XCTAssertEqual(model.current?.translation, "Bienvenidos.")
        model.tick(at: 4, layout: layout)
        XCTAssertEqual(model.current?.translation, "Uno. Dos. Tres.")
        model.receive([first, entry(2, "Ninety.", "Noventa.", state: .polished)] + Array(burst.suffix(2)), at: 5)
        model.tick(at: 5, layout: layout)
        XCTAssertEqual(model.current?.translation, "Noventa. Dos. Tres.", "Corrections retain their identity within a combined page")
        XCTAssertEqual(model.holdUntil, 9)
    }

    func testRepeatedPartialSnapshotsDoNotPretendToBeNewEvidence() {
        var model = ProjectorCaptionPresentation()
        let partial = entry(1, "Welcome everyone ", "Bienvenidos todos ", state: .partial)
        model.receive([partial], at: 0)
        for step in 1...6 {
            model.receive([partial], at: Double(step) / 10)
            model.tick(at: Double(step) / 10, layout: layout)
            XCTAssertNil(model.draft)
        }
        model.tick(at: 0.8, layout: layout)
        XCTAssertEqual(model.draft?.translation, "Bienvenidos todos")
    }

    func testFontIsIndependentOfCaptionLengthAndPagesFitSupportedSizes() {
        for size in [CGSize(width: 1280, height: 720), CGSize(width: 1920, height: 1080)] {
            for style in ProjectorPresentationStyle.allCases {
                for requested in [36.0, 72.0, 144.0] {
                    let layout = ProjectorCaptionLayout(size: size, requestedFontSize: requested, style: style)
                    let font = layout.fontSize
                    _ = layout.prefix(String(repeating: "Long caption words. ", count: 40), source: false)
                    XCTAssertEqual(font, layout.fontSize)
                    let laneHeight = style == .stack ? (layout.mainHeight - 24) / 2 : layout.mainHeight
                    XCTAssertLessThanOrEqual(layout.textHeight(source: false) * 2 + 56, laneHeight)
                }
            }
        }
    }

    func testLatePartialCannotOverwriteFinalOrPolishedCaption() async {
        await MainActor.run {
            let store = TranscriptStore()
            store.handle(.caption(type: .final, utteranceID: 1, original: "Ninety.", translation: "Noventa."))
            store.handle(.caption(type: .partial, utteranceID: 1, original: "Nineteen", translation: "Diecinueve"))
            XCTAssertEqual(store.entries[0].translation, "Noventa.")
            store.handle(.caption(type: .polished, utteranceID: 1, original: "Ninety!", translation: "¡Noventa!"))
            store.handle(.caption(type: .final, utteranceID: 1, original: "Ninety.", translation: "Noventa."))
            XCTAssertEqual(store.entries[0].state, .polished)
        }
    }

    func testPreviousPassageSurvivesNextPagesReadingTimeAndSilence() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "Welcome.", "Bienvenidos.")
        let second = entry(2, "Please sit.", "Siéntense.")
        let third = entry(3, "Thank you.", "Gracias.")
        model.receive([first], at: 0)
        model.tick(at: 0, layout: layout)
        model.receive([first, second], at: 1)
        model.tick(at: 4, layout: layout)
        XCTAssertEqual(model.previous?.translation, first.translation)
        XCTAssertEqual(model.current?.translation, second.translation)
        model.receive([first, second, third], at: 5)
        model.tick(at: 7.99, layout: layout)
        XCTAssertEqual(model.previous?.translation, first.translation)
        model.tick(at: 8, layout: layout)
        XCTAssertEqual(model.previous?.translation, second.translation)
        XCTAssertEqual(model.current?.translation, third.translation)
        model.tick(at: 800, layout: layout)
        XCTAssertEqual(model.previous?.translation, second.translation)
        XCTAssertEqual(model.current?.translation, third.translation)
        model.receive([], at: 801)
        XCTAssertNil(model.previous)
    }

    func testLongUtteranceRetainsPreviousPageAndResizeDoesNotDuplicateCurrent() {
        var model = ProjectorCaptionPresentation()
        let text = String(repeating: "A longer sentence with more words. ", count: 8)
        model.receive([entry(1, text, text)], at: 0)
        model.tick(at: 0, layout: layout)
        let firstPage = model.current
        model.tick(at: model.holdUntil, layout: layout)
        XCTAssertEqual(model.previous, firstPage)
        XCTAssertTrue(model.current!.isContinuation)
        let resized = ProjectorCaptionLayout(size: CGSize(width: 1920, height: 1080), requestedFontSize: 72, style: .split)
        model.tick(at: 10, layout: resized)
        XCTAssertEqual(model.previous, firstPage)
    }

    func testCorrectionWithdrawsObsoleteVisibleHistory() {
        var model = ProjectorCaptionPresentation()
        let first = entry(1, "Nineteen.", "Diecinueve.")
        let second = entry(2, "Thanks.", "Gracias.")
        model.receive([first], at: 0)
        model.tick(at: 0, layout: layout)
        model.receive([first, second], at: 1)
        model.tick(at: 4, layout: layout)
        XCTAssertNotNil(model.previous)
        model.receive([entry(1, "Ninety.", "Noventa.", state: .polished), second], at: 5)
        model.tick(at: 5, layout: layout)
        XCTAssertNil(model.previous)
        XCTAssertEqual(model.current?.translation, "Gracias.")
    }

    func testRenderAudienceLayouts() async throws {
        guard let output = ProcessInfo.processInfo.environment["LIVETR3_PROJECTOR_RENDER_DIR"] else {
            throw XCTSkip("Set LIVETR3_PROJECTOR_RENDER_DIR to render the audience layouts")
        }
        try await MainActor.run {
            try FileManager.default.createDirectory(atPath: output, withIntermediateDirectories: true)
            for size in [CGSize(width: 1280, height: 720), CGSize(width: 1920, height: 1080)] {
                for style in ProjectorPresentationStyle.allCases {
                    let layout = ProjectorCaptionLayout(size: size, requestedFontSize: 72, style: style)
                    var model = ProjectorCaptionPresentation()
                    let final = entry(1, "Let us love one another.", "Amémonos unos a otros.")
                    model.receive([final], at: 0)
                    model.tick(at: 0, layout: layout)
                    let second = entry(2, "Love comes from God.", "El amor viene de Dios.")
                    model.receive([final, second], at: 1)
                    model.tick(at: model.holdUntil, layout: layout)
                    model.receive([final, second, entry(3, "Everyone who loves ", "Todo el que ama ", state: .partial)], at: 5)
                    model.tick(at: 6, layout: layout)
                    let stage = ProjectorCaptionStage(current: model.current, previous: model.previous, draft: model.draft, layout: layout,
                                                       sourceLanguage: "English", targetLanguage: "Spanish")
                    let renderer = ImageRenderer(content: stage)
                    renderer.proposedSize = ProposedViewSize(size)
                    let image = try XCTUnwrap(renderer.nsImage)
                    let bitmap = try XCTUnwrap(NSBitmapImageRep(data: try XCTUnwrap(image.tiffRepresentation)))
                    let data = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
                    try data.write(to: URL(fileURLWithPath: output).appendingPathComponent("\(style.rawValue)-\(Int(size.width)).png"))
                }
            }
        }
    }
}
