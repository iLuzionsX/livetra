import Foundation

@MainActor
final class LiveTR3Runtime: ObservableObject {
    enum State: Equatable {
        case idle
        case starting
        case ready
        case failed
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var statusMessage = "Local engine is not running."

    nonisolated static var engineSocketPath: URL {
        if let configuredPath = ProcessInfo.processInfo.environment["LIVETR3_ENGINE_SOCKET"],
           !configuredPath.isEmpty {
            return URL(fileURLWithPath: configuredPath)
        }

        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "LiveTR3")
            .appending(path: "Runtime")
        return base.appending(path: "engine.sock")
    }

    private let repoRoot: URL
    private let logDirectory: URL
    private var backendProcess: Process?
    private var ownsBackendProcess = false

    init() {
        self.repoRoot = Self.findRepoRoot()
        self.logDirectory = Self.findRepoRoot().appending(path: "dist/logs")
    }

    func start() async {
        guard state != .starting && state != .ready else { return }

        state = .starting
        statusMessage = "Starting local caption engine..."

        do {
            try launchBackend()
            try await waitForReady()
            state = .ready
            statusMessage = "Local engine is ready."
        } catch {
            state = .failed
            statusMessage = error.localizedDescription
            stop()
        }
    }

    func restart() async {
        stop()
        await start()
    }

    func stop() {
        if ownsBackendProcess {
            backendProcess?.terminate()
        }
        backendProcess = nil
        ownsBackendProcess = false
        if state != .failed {
            state = .idle
            statusMessage = "Local engine is stopped."
        }
    }

    private func launchBackend() throws {
        try FileManager.default.createDirectory(
            at: Self.engineSocketPath.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try? FileManager.default.removeItem(at: Self.engineSocketPath)

        let bundledBackend = Bundle.main.bundleURL
            .appending(path: "Contents")
            .appending(path: "Resources")
            .appending(path: "Engine")
            .appending(path: "backend")
        let devBackend = repoRoot.appending(path: "app/backend")
        let bundledVenvPython = Bundle.main.bundleURL
            .appending(path: "Contents")
            .appending(path: "Resources")
            .appending(path: "Engine")
            .appending(path: "venv/bin/python")
        let bundledPython = Bundle.main.bundleURL
            .appending(path: "Contents")
            .appending(path: "Resources")
            .appending(path: "Engine")
            .appending(path: "python/bin/python3")
        let packagedPython = FileManager.default.fileExists(atPath: bundledVenvPython.path)
            ? bundledVenvPython
            : bundledPython

        if FileManager.default.fileExists(atPath: packagedPython.path) {
            let backend = bundledBackend
            backendProcess = try launch(
                executable: packagedPython.path,
                arguments: ["uds_host.py"],
                workingDirectory: backend
            )
        } else if FileManager.default.isExecutableFile(atPath: devBackend.appending(path: ".venv/bin/python").path) {
            backendProcess = try launch(
                executable: devBackend.appending(path: ".venv/bin/python").path,
                arguments: ["uds_host.py"],
                workingDirectory: devBackend
            )
        } else {
            let backend = devBackend
            backendProcess = try launch(
                executable: "/bin/zsh",
                arguments: [
                    "-lc",
                    "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin uv run python uds_host.py"
                ],
                workingDirectory: backend
            )
        }
        ownsBackendProcess = true
    }

    private func launch(executable: String, arguments: [String], workingDirectory: URL) throws -> Process {
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
        let logURL = logDirectory.appending(path: "\(workingDirectory.lastPathComponent)-runtime.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try FileHandle(forWritingTo: logURL)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = workingDirectory
        process.environment = Self.engineEnvironment(backend: workingDirectory)
        process.standardOutput = logHandle
        process.standardError = logHandle
        try process.run()
        return process
    }

    private func waitForReady() async throws {
        try await waitUntil("Local engine did not create its Unix socket.") {
            FileManager.default.fileExists(atPath: Self.engineSocketPath.path)
        }
    }

    private func waitUntil(_ timeoutMessage: String, check: @escaping () async -> Bool) async throws {
        for _ in 0..<80 {
            if await check() { return }
            if let backendProcess, !backendProcess.isRunning {
                throw RuntimeError(message: "Local engine exited before becoming ready. Check dist/logs/backend-runtime.log.")
            }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        throw RuntimeError(message: timeoutMessage)
    }

    private static func findRepoRoot() -> URL {
        let bundleParent = Bundle.main.bundleURL.deletingLastPathComponent()
        if bundleParent.lastPathComponent == "dist" {
            return bundleParent.deletingLastPathComponent()
        }

        var sourceURL = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 {
            sourceURL.deleteLastPathComponent()
        }
        return sourceURL
    }

    private static func engineEnvironment(backend: URL) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["LIVETR3_ENGINE_SOCKET"] = engineSocketPath.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONPATH"] = backend.path
        environment["HF_HUB_OFFLINE"] = environment["HF_HUB_OFFLINE"] ?? "1"
        environment["TRANSFORMERS_OFFLINE"] = environment["TRANSFORMERS_OFFLINE"] ?? "1"
        return environment
    }

    private struct RuntimeError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }
}
