import SwiftUI

struct OperatorWorkspace: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @EnvironmentObject private var session: SessionController
    @EnvironmentObject private var sessionManager: SessionManager
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Group {
            if runtime.state == .ready {
                TeleprompterSessionSheetOperatorView(
                    session: session,
                    sessionManager: sessionManager,
                    onOpenProjector: openProjector
                )
            } else {
                RuntimeOverlay(state: runtime.state, message: runtime.statusMessage)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
    }

    private func openProjector() {
        openWindow(id: LiveTR3WindowID.projector)
    }
}
