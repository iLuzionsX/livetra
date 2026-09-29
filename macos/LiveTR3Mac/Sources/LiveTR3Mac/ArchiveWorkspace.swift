import SwiftUI

struct ArchiveWorkspace: View {
    var body: some View {
        ContentUnavailableView(
            "No Archive Browser Yet",
            systemImage: "clock.arrow.circlepath",
            description: Text("Session archives stay in Application Support.")
        )
        .padding(32)
    }
}
