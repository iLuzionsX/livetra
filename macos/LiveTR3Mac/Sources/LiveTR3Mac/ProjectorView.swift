import Combine
import SwiftUI

struct ProjectorView: View {
    @ObservedObject var connection: ProjectorConnection
    @ObservedObject var sessionManager: SessionManager
    @EnvironmentObject private var session: SessionController

    var body: some View {
        ProjectorTranscriptView(
            transcript: connection.transcript,
            sessionManager: sessionManager,
            sourceLanguage: session.config.source_lang,
            targetLanguage: session.config.target_lang,
            connectionError: connection.connectionError
        )
        .onAppear { connection.connect() }
        .onDisappear { connection.disconnect() }
    }
}

/// Observe the store itself: observing its owner does not forward nested changes.
private struct ProjectorTranscriptView: View {
    @ObservedObject var transcript: TranscriptStore
    @ObservedObject var sessionManager: SessionManager
    let sourceLanguage: String
    let targetLanguage: String
    let connectionError: String?
    @State private var presentation = ProjectorCaptionPresentation()
    private let clock = Timer.publish(every: 0.1, on: .main, in: .common).autoconnect()

    var body: some View {
        GeometryReader { geometry in
            let layout = ProjectorCaptionLayout(
                size: geometry.size,
                requestedFontSize: sessionManager.projectorFontSize,
                style: sessionManager.projectorStyle
            )
            ProjectorCaptionStage(
                current: presentation.current,
                previous: presentation.previous,
                draft: presentation.draft,
                layout: layout,
                sourceLanguage: sourceLanguage,
                targetLanguage: targetLanguage,
                status: connectionError ?? transcript.lastError
            )
            .onAppear { receive(layout: layout) }
            .onChange(of: transcript.entries) { _, _ in receive(layout: layout) }
            .onChange(of: layout) { _, _ in advance(layout: layout) }
            .onReceive(clock) { _ in advance(layout: layout) }
        }
    }

    private func receive(layout: ProjectorCaptionLayout) {
        let now = ProcessInfo.processInfo.systemUptime
        presentation.receive(transcript.entries, at: now)
        presentation.tick(at: now, layout: layout)
    }

    private func advance(layout: ProjectorCaptionLayout) {
        presentation.tick(at: ProcessInfo.processInfo.systemUptime, layout: layout)
    }
}

/// Kept separate from the connection so the real audience surface can be rendered in tests.
struct ProjectorCaptionStage: View {
    let current: ProjectorCaptionPresentation.Caption?
    var previous: ProjectorCaptionPresentation.Caption? = nil
    let draft: ProjectorCaptionPresentation.Caption?
    let layout: ProjectorCaptionLayout
    let sourceLanguage: String
    let targetLanguage: String
    var status: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            mainCaption
                .frame(height: layout.mainHeight, alignment: .topLeading)

            VStack(alignment: .leading, spacing: 12) {
                Rectangle().fill(.white.opacity(0.2)).frame(height: 1)
                if let current, let draft, current.utteranceID != draft.utteranceID {
                    label("Live draft · may change", color: .white.opacity(0.8))
                    draftCaption(draft)
                } else if let status {
                    label("Caption connection needs attention", color: .orange)
                        .accessibilityHint(status)
                }
            }
            .frame(height: layout.draftHeight, alignment: .topLeading)
        }
        .padding(layout.inset)
        .frame(width: layout.size.width, height: layout.size.height, alignment: .topLeading)
        .background(.black)
        .foregroundStyle(.white)
        .transaction { $0.animation = nil }
    }

    @ViewBuilder
    private var mainCaption: some View {
        if let caption = current ?? draft {
            let isDraft = current == nil
            let source = isDraft ? layout.prefix(caption.original, source: true, balancePages: false) : caption.original
            let target = isDraft ? layout.prefix(caption.translation, source: false, balancePages: false) : caption.translation
            switch layout.style {
            case .focus:
                lane(target, previous: previous?.translation, source: false,
                     title: title(targetLanguage, draft: isDraft, continuation: caption.isContinuation),
                     fontSize: layout.fontSize, language: targetLanguage)
            case .split:
                HStack(alignment: .top, spacing: 24) {
                    lane(source, previous: previous?.original, source: true,
                         title: title(sourceLanguage, draft: isDraft, continuation: caption.isContinuation),
                         fontSize: layout.sourceFontSize, language: sourceLanguage)
                    Rectangle().fill(.white.opacity(0.25)).frame(width: 1)
                    lane(target, previous: previous?.translation, source: false,
                         title: title(targetLanguage, draft: isDraft, continuation: caption.isContinuation),
                         fontSize: layout.fontSize, language: targetLanguage)
                }
            case .stack:
                VStack(alignment: .leading, spacing: 24) {
                    lane(source, previous: previous?.original, source: true,
                         title: title(sourceLanguage, draft: isDraft, continuation: caption.isContinuation),
                         fontSize: layout.sourceFontSize, language: sourceLanguage)
                        .frame(height: (layout.mainHeight - 24) / 2, alignment: .top)
                    lane(target, previous: previous?.translation, source: false,
                         title: title(targetLanguage, draft: isDraft, continuation: caption.isContinuation),
                         fontSize: layout.fontSize, language: targetLanguage)
                }
            }
        } else {
            Text("Listening…")
                .font(.system(size: layout.fontSize, weight: .medium))
                .foregroundStyle(.white.opacity(0.75))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
        }
    }

    private func title(_ language: String, draft: Bool, continuation: Bool) -> String {
        language + (draft ? " · Live draft" : continuation ? " · Continued" : "")
    }

    private func lane(_ text: String, previous: String?, source: Bool, title: String,
                      fontSize: CGFloat, language: String) -> some View {
        VStack(alignment: isRtlLanguage(language) ? .trailing : .leading, spacing: 12) {
            label(title)
            VStack(spacing: 20) {
                // Refit history only for an explicit window/font/layout change. Ordinary
                // arrivals keep the entire preceding page in this fixed reading slot.
                captionText(layout.prefix(previous ?? "", source: source, balancePages: false)
                    .trimmingCharacters(in: .whitespacesAndNewlines), fontSize: fontSize, language: language)
                    .foregroundStyle(Color.white.opacity(0.88))
                    .frame(height: layout.textHeight(source: source), alignment: .top)
                captionText(text.trimmingCharacters(in: .whitespacesAndNewlines), fontSize: fontSize, language: language)
                    .frame(height: layout.textHeight(source: source), alignment: .top)
            }
        }
        .frame(maxWidth: .infinity, alignment: isRtlLanguage(language) ? .topTrailing : .topLeading)
    }

    @ViewBuilder
    private func draftCaption(_ draft: ProjectorCaptionPresentation.Caption) -> some View {
        if layout.style == .split {
            HStack(alignment: .top, spacing: 48) {
                draftText(draft.original, language: sourceLanguage, width: layout.columnWidth)
                draftText(draft.translation, language: targetLanguage, width: layout.columnWidth)
            }
        } else {
            draftText(draft.translation, language: targetLanguage, width: layout.columnWidth)
        }
    }

    private func draftText(_ text: String, language: String, width: CGFloat) -> some View {
        captionText(layout.draftExcerpt(text, width: width), fontSize: layout.draftFontSize, language: language)
            .foregroundStyle(Color(red: 0.91, green: 0.94, blue: 1))
    }

    private func captionText(_ text: String, fontSize: CGFloat, language: String) -> some View {
        Text(text)
            .font(.system(size: fontSize, weight: .semibold))
            .lineSpacing(layout.lineSpacing)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(isRtlLanguage(language) ? .trailing : .leading)
            .frame(maxWidth: .infinity, alignment: isRtlLanguage(language) ? .trailing : .leading)
            .environment(\.layoutDirection, isRtlLanguage(language) ? .rightToLeft : .leftToRight)
    }

    private func label(_ text: String, color: Color = .white.opacity(0.72)) -> some View {
        Text(text)
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(color)
    }
}
