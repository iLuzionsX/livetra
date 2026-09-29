import SwiftUI

@main
struct LiveTR3App: App {
    @StateObject private var runtime: LiveTR3Runtime
    @StateObject private var sessionManager: SessionManager
    @StateObject private var sessionController: SessionController
    @AppStorage("LiveTR3.startsRuntimeAutomatically") private var startsRuntimeAutomatically = true
    @Environment(\.openWindow) private var openWindow

    init() {
        let runtime = LiveTR3Runtime()
        let manager = SessionManager()
        _runtime = StateObject(wrappedValue: runtime)
        _sessionManager = StateObject(wrappedValue: manager)
        _sessionController = StateObject(wrappedValue: SessionController(sessionManager: manager, runtime: runtime))
    }

    var body: some Scene {
        WindowGroup {
            rootContent
        }
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Restart Local Runtime") {
                    Task { await runtime.restart() }
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])

                Button("Open Projector") {
                    openWindow(id: LiveTR3WindowID.projector)
                }
                .keyboardShortcut("p", modifiers: [.command, .shift])
                .disabled(runtime.state != .ready)
            }
        }

        Window("Projector", id: LiveTR3WindowID.projector) {
            ProjectorWindowRoot()
                .environmentObject(sessionManager)
                .environmentObject(sessionController)
        }
        .defaultSize(width: 1440, height: 900)

        Settings {
            SettingsView()
        }
    }

    private var rootContent: some View {
        OperatorWorkspace()
            .environmentObject(runtime)
            .environmentObject(sessionManager)
            .environmentObject(sessionController)
            .frame(minWidth: 1120, minHeight: 760)
            .task {
                guard startsRuntimeAutomatically else { return }
                await runtime.start()
            }
            .onDisappear {
                runtime.stop()
            }
    }
}
