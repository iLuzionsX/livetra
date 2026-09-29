import AVFoundation

/// Keeps resampling phase and anti-alias filtering continuous across microphone buffers.
final class StreamingAudioConverter {
    private let converter: AVAudioConverter
    private let outputFormat: AVAudioFormat

    init(inputFormat: AVAudioFormat) throws {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000,
                                        channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: inputFormat, to: format) else {
            throw LiveTR3SessionError(message: "Could not configure microphone audio conversion.")
        }
        outputFormat = format
        self.converter = converter
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
    }

    func reset() { converter.reset() }

    func convert(_ input: AVAudioPCMBuffer) throws -> [Float] {
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * 16_000 / input.format.sampleRate)) + 256
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return [] }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return input
        }
        if let error { throw error }
        guard status != .error, let samples = output.floatChannelData?[0] else { return [] }
        return Array(UnsafeBufferPointer(start: samples, count: Int(output.frameLength)))
    }
}
