import AVFoundation
import XCTest
@testable import LiveTR3Mac

final class StreamingAudioConverterTests: XCTestCase {
    func testContinuousResamplingDoesNotLoseSamplesAtBufferBoundaries() throws {
        for rate in [44_100.0, 48_000.0] {
            let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1))
            let converter = try StreamingAudioConverter(inputFormat: format)
            var count = 0
            for _ in 0..<1000 {
                let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1024))
                buffer.frameLength = 1024
                buffer.floatChannelData![0].initialize(repeating: 0.1, count: 1024)
                count += try converter.convert(buffer).count
            }
            // Allow fixed converter priming latency, but no cumulative per-buffer drift.
            XCTAssertEqual(Double(count), 1_024_000 * 16_000 / rate, accuracy: 256)
        }
    }

    func testDownsamplingRejectsAboveNyquistEnergy() throws {
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 1))
        let converter = try StreamingAudioConverter(inputFormat: format)
        var samples: [Float] = []
        for chunk in 0..<100 {
            let buffer = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1024))
            buffer.frameLength = 1024
            for index in 0..<1024 {
                buffer.floatChannelData![0][index] = Float(sin(2 * .pi * 12_000 * Double(chunk * 1024 + index) / 48_000))
            }
            samples += try converter.convert(buffer)
        }
        let steady = samples.dropFirst(1000)
        let rms = sqrt(steady.reduce(0.0) { $0 + Double($1 * $1) } / Double(steady.count))
        XCTAssertLessThan(rms, 0.01)
    }
}
