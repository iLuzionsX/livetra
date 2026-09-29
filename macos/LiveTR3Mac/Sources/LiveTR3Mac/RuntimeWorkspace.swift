import SwiftUI

struct RuntimeWorkspace: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @AppStorage("LiveTR3.showsAdvancedRuntimeDetails") private var showsAdvancedRuntimeDetails = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHero(
                    title: "Local Engine",
                    subtitle: runtime.statusMessage,
                    symbolName: runtime.state.symbolName
                )

                HStack(spacing: 12) {
                    RuntimeMetric(title: "Transport", value: "UDS", detail: runtime.state == .ready ? "Ready" : runtime.state.label)
                    RuntimeMetric(title: "Operator UI", value: "Native", detail: runtime.state == .ready ? "SwiftUI" : "Waiting")
                    RuntimeMetric(title: "Engine", value: "Gemma", detail: "Local speech")
                }

                if showsAdvancedRuntimeDetails {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Local Engine IPC")
                            .font(.headline)
                        Text("Unix socket: \(LiveTR3Runtime.engineSocketPath.path)")
                            .textSelection(.enabled)
                    }
                    .font(.callout.monospaced())
                    .foregroundStyle(.secondary)
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .liveGlassSurface(cornerRadius: 16)
                }

                Button {
                    Task { await runtime.restart() }
                } label: {
                    Label("Restart Local Engine", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderedProminent)
                .disabled(runtime.state == .starting)
            }
            .padding(32)
        }
    }
}
