import AppKit
import SwiftUI

struct NativeOperatorView: View {
    @ObservedObject var session: SessionController
    @ObservedObject var sessionManager: SessionManager
    let onOpenProjector: () -> Void

    @State private var settingsOpen = false

    var body: some View {
        VStack(spacing: 0) {
            OperatorHeaderView(
                session: session,
                sessionManager: sessionManager,
                settingsOpen: $settingsOpen,
                onOpenProjector: onOpenProjector
            )

            if let workerStatus = session.transcript.workerStatus, workerStatus.state != .ready {
                banner(text: workerStatus.message, tint: .orange)
            }

            if let error = displayedError {
                banner(text: error, tint: .red)
            }

            DualPaneView(
                entries: session.transcript.entries,
                sourceLanguage: session.config.source_lang,
                targetLanguage: session.config.target_lang
            )
        }
        .background(.background)
        .onAppear {
            session.refreshDevices()
        }
        .background {
            KeyboardShortcutMonitor(
                onStartStop: session.startStop,
                onExportTXT: exportTXT
            )
        }
    }

    private var displayedError: String? {
        session.transcript.lastError ?? session.error
    }

    @ViewBuilder
    private func banner(text: String, tint: Color) -> some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(tint == .red ? Color.red.opacity(0.9) : Color.orange.opacity(0.95))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
            .background(tint.opacity(0.12))
    }

    private func exportTXT() {
        TranscriptExporter.exportTXT(
            entries: session.transcript.entries,
            source: session.config.source_lang,
            target: session.config.target_lang
        )
    }
}

private struct KeyboardShortcutMonitor: NSViewRepresentable {
    let onStartStop: () -> Void
    let onExportTXT: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        context.coordinator.start(
            onStartStop: onStartStop,
            onExportTXT: onExportTXT
        )
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        context.coordinator.updateHandlers(
            onStartStop: onStartStop,
            onExportTXT: onExportTXT
        )
    }

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.stop()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    final class Coordinator {
        private var monitor: Any?
        private var onStartStop: (() -> Void)?
        private var onExportTXT: (() -> Void)?

        func start(
            onStartStop: @escaping () -> Void,
            onExportTXT: @escaping () -> Void
        ) {
            updateHandlers(
                onStartStop: onStartStop,
                onExportTXT: onExportTXT
            )
            guard monitor == nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self else { return event }
                guard !self.isTypingTarget(event) else { return event }

                if event.keyCode == 49, event.modifierFlags.intersection(.deviceIndependentFlagsMask).isEmpty {
                    self.onStartStop?()
                    return nil
                }
                if event.modifierFlags.contains(.command), event.charactersIgnoringModifiers?.lowercased() == "e" {
                    self.onExportTXT?()
                    return nil
                }
                return event
            }
        }

        func updateHandlers(
            onStartStop: @escaping () -> Void,
            onExportTXT: @escaping () -> Void
        ) {
            self.onStartStop = onStartStop
            self.onExportTXT = onExportTXT
        }

        func stop() {
            if let monitor {
                NSEvent.removeMonitor(monitor)
            }
            monitor = nil
        }

        private func isTypingTarget(_ event: NSEvent) -> Bool {
            guard let responder = event.window?.firstResponder else { return false }
            return responder is NSTextView || responder is NSTextField
        }
    }
}