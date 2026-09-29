import SwiftUI

struct DashboardMetric: View {
    let title: String
    let value: String
    let detail: String
    let symbolName: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbolName)
                .font(.title3)
                .foregroundStyle(.secondary)
                .frame(width: 28, height: 28)

            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.title3.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
        .padding(16)
        .liveGlassSurface(cornerRadius: 18)
    }
}

struct FloatingRuntimeControls: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime

    var body: some View {
        HStack(spacing: 12) {
            RuntimePill(
                title: "Local engine",
                value: runtime.state.label,
                symbolName: runtime.state.symbolName,
                tint: runtime.state.tint
            )

            RuntimePill(
                title: "Transport",
                value: runtime.state == .ready ? "Unix socket" : "Starting",
                symbolName: "point.3.connected.trianglepath.dotted",
                tint: .secondary
            )

            Spacer(minLength: 12)

            Button {
                Task { await runtime.restart() }
            } label: {
                Label("Restart", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.capsule)
            .controlSize(.regular)
            .disabled(runtime.state == .starting)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .liveGlassSurface(cornerRadius: 18, interactive: true)
        .liveGlassGroup()
    }
}

private struct RuntimePill: View {
    let title: String
    let value: String
    let symbolName: String
    let tint: Color

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
            }
        } icon: {
            Image(systemName: symbolName)
                .foregroundStyle(tint)
                .frame(width: 18)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
    }
}

struct RuntimeOverlay: View {
    let state: LiveTR3Runtime.State
    let message: String

    var body: some View {
        VStack(spacing: 14) {
            ProgressView()
                .controlSize(.large)
                .opacity(state == .failed ? 0 : 1)
            Image(systemName: state.symbolName)
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(state.tint)
            Text(state.label)
                .font(.title2.weight(.semibold))
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 480)
        }
        .padding(30)
        .liveGlassSurface(cornerRadius: 26)
    }
}

struct SectionHero: View {
    let title: String
    let subtitle: String
    let symbolName: String

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: symbolName)
                .font(.system(size: 28, weight: .semibold))
                .frame(width: 52, height: 52)
                .liveGlassSurface(cornerRadius: 14)

            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.largeTitle.weight(.semibold))
                Text(subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()
        }
        .padding(20)
        .liveGlassSurface(cornerRadius: 20)
    }
}

struct RuntimeMetric: View {
    let title: String
    let value: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.weight(.semibold))
            Text(detail)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .liveGlassSurface(cornerRadius: 20)
    }
}
