import SwiftUI

struct DetailRoot: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @EnvironmentObject private var sessionManager: SessionManager
    @EnvironmentObject private var sessionController: SessionController
    let selection: LiveTR3Section

    var body: some View {
        Group {
            switch selection {
            case .operatorPanel:
                OperatorWorkspace()
                    .environmentObject(runtime)
                    .environmentObject(sessionManager)
                    .environmentObject(sessionController)
            case .projector:
                ProjectorWorkspace()
                    .environmentObject(runtime)
                    .environmentObject(sessionManager)
                    .environmentObject(sessionController)
            case .runtime:
                RuntimeWorkspace()
                    .environmentObject(runtime)
            case .archive:
                ArchiveWorkspace()
            }
        }
        .navigationTitle(selection.title)
    }
}
