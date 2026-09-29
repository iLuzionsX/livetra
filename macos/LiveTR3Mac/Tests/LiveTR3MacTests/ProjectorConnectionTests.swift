import Combine
import XCTest
@testable import LiveTR3Mac

@MainActor
final class ProjectorConnectionTests: XCTestCase {
    func testTranscriptChangesInvalidateProjectorConnection() {
        let connection = ProjectorConnection(sessionID: "projector-publisher-test")
        var didPublish = false
        let observation = connection.objectWillChange.sink { _ in
            didPublish = true
        }

        connection.transcript.handle(
            .caption(type: .partial, utteranceID: 1, original: "Hello", translation: "Hola")
        )

        XCTAssertTrue(didPublish)
        withExtendedLifetime(observation) {}
    }
}
