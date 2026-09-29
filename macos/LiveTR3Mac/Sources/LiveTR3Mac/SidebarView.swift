import SwiftUI

struct SidebarView: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime
    @Binding var selection: LiveTR3Section?

    var body: some View {
        List(selection: $selection) {
            Section {
                ForEach(LiveTR3Section.primary) { section in
                    NavigationLink(value: section) {
                        SidebarRow(section: section)
                    }
                }
            }

            Section("System") {
                ForEach(LiveTR3Section.system) { section in
                    NavigationLink(value: section) {
                        SidebarRow(section: section)
                    }
                }
            }

            Section {
                SidebarStatusRow()
                    .environmentObject(runtime)
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("LiveTR3")
    }
}

private struct SidebarRow: View {
    let section: LiveTR3Section

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(section.title)
                    .lineLimit(1)
                Text(section.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        } icon: {
            Image(systemName: section.symbolName)
                .foregroundStyle(.secondary)
                .frame(width: 18)
        }
    }
}

private struct SidebarStatusRow: View {
    @EnvironmentObject private var runtime: LiveTR3Runtime

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 2) {
                Text(runtime.state.label)
                    .lineLimit(1)
                Text(runtime.statusMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        } icon: {
            Image(systemName: runtime.state.symbolName)
                .foregroundStyle(runtime.state.tint)
                .frame(width: 18)
        }
    }
}
