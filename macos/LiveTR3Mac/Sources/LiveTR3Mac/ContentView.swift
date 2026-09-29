import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @EnvironmentObject private var sessionManager: SessionManager
    @EnvironmentObject private var sessionController: SessionController
    @SceneStorage("LiveTR3.selectedSection") private var selectedSectionRaw = LiveTR3Section.operatorPanel.rawValue

    private var selectedSection: LiveTR3Section {
        LiveTR3Section(rawValue: selectedSectionRaw) ?? .operatorPanel
    }

    private var sidebarSelection: Binding<LiveTR3Section?> {
        Binding(
            get: { selectedSection },
            set: { newValue in
                guard let newValue else { return }
                selectedSectionRaw = newValue.rawValue
            }
        )
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(selection: sidebarSelection)
                .environmentObject(runtime)
                .navigationSplitViewColumnWidth(min: 230, ideal: 270, max: 320)
        } detail: {
            DetailRoot(selection: selectedSection)
                .environmentObject(runtime)
                .environmentObject(sessionManager)
                .environmentObject(sessionController)
        }
        .navigationSplitViewStyle(.balanced)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await runtime.restart() }
                } label: {
                    Label("Restart Runtime", systemImage: "arrow.clockwise")
                }
                .disabled(runtime.state == .starting)
            }

            ToolbarItem(placement: .automatic) {
                SettingsLink {
                    Label("Settings", systemImage: "gearshape")
                }
            }
        }
    }
}
