import SwiftUI

struct DualPaneView: View {
    let entries: [TranscriptUtterance]
    let sourceLanguage: String
    let targetLanguage: String

    var body: some View {
        VStack(spacing: 0) {
            transcriptPane(
                title: sourceLanguage,
                field: .original,
                layoutDirection: isRtlLanguage(sourceLanguage) ? .rightToLeft : .leftToRight
            )
            Divider()
            transcriptPane(
                title: targetLanguage,
                field: .translation,
                layoutDirection: isRtlLanguage(targetLanguage) ? .rightToLeft : .leftToRight
            )
        }
        .background(.background)
    }

    @ViewBuilder
    private func transcriptPane(
        title: String,
        field: TranscriptLineView.TranscriptField,
        layoutDirection: LayoutDirection
    ) -> some View {
        VStack(spacing: 0) {
            HStack {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
            .background(.bar)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(entries.filter { !field.text(from: $0).isEmpty }) { entry in
                            TranscriptLineView(
                                entry: entry,
                                field: field,
                                layoutDirection: layoutDirection
                            )
                            .id(entry.id)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 14)
                }
                .onChange(of: entries.count) { _, _ in
                    if let last = entries.last {
                        withAnimation(.easeOut(duration: 0.15)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                .onChange(of: entries.last?.translation) { _, _ in
                    if let last = entries.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
                .onChange(of: entries.last?.original) { _, _ in
                    if let last = entries.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
