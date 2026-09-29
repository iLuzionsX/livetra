import SwiftUI

struct ProjectorWorkspace: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @EnvironmentObject private var sessionManager: SessionManager
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            SectionHero(
                title: "Projector",
                subtitle: runtime.state == .ready ? "Native audience window is available." : runtime.statusMessage,
                symbolName: "rectangle.on.rectangle"
            )

            HStack(spacing: 12) {
                DashboardMetric(title: "Route", value: "Projector", detail: "Native audience view", symbolName: "display")
                DashboardMetric(
                    title: "Runtime",
                    value: runtime.state.label,
                    detail: runtime.state == .ready ? "Local engine ready" : "Waiting",
                    symbolName: runtime.state.symbolName
                )
            }

            VStack(alignment: .leading, spacing: 12) {
                Text("Session")
                    .font(.headline)
                Text(sessionManager.sessionID)
                    .font(.callout.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)

                Button {
                    openWindow(id: LiveTR3WindowID.projector)
                } label: {
                    Label("Open projector window", systemImage: "arrow.up.forward.app")
                }
                .buttonStyle(.borderedProminent)
                .disabled(runtime.state != .ready)
            }
            .padding(18)
            .liveGlassSurface(cornerRadius: 20)
        }
        .padding(32)
    }
}

struct ProjectorWindowRoot: View {
    @EnvironmentObject private var sessionManager: SessionManager

    var body: some View {
        ProjectorContainer(sessionID: sessionManager.sessionID)
            .environmentObject(sessionManager)
    }
}

private struct ProjectorContainer: View {
    @StateObject private var connection: ProjectorConnection
    @EnvironmentObject private var sessionManager: SessionManager

    init(sessionID: String) {
        _connection = StateObject(wrappedValue: ProjectorConnection(sessionID: sessionID))
    }

    var body: some View {
        ProjectorView(connection: connection, sessionManager: sessionManager)
            .frame(minWidth: 1_280, minHeight: 720)
    }
}
