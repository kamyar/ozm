import Foundation

/// One newline-delimited JSON request, accumulated across stream reads.
struct RequestFrame: Sendable {
    enum State {
        case waiting
        case complete(Data)
        case invalid
    }

    static let maximumBytes = 1_048_576
    private var buffer = Data()

    mutating func append(_ data: Data, endOfStream: Bool = false) -> State {
        guard data.count <= Self.maximumBytes - buffer.count else {
            return .invalid
        }
        let previousEnd = buffer.endIndex
        buffer.append(data)
        // Earlier chunks had no delimiter. Scan only the newly appended bytes.
        guard let newline = buffer[previousEnd...].firstIndex(of: 0x0A) else {
            return endOfStream ? .invalid : .waiting
        }
        // Each connection carries one request. Do not accept a second message.
        let trailing = buffer[buffer.index(after: newline)...]
        guard trailing.allSatisfy({ [0x09, 0x0A, 0x0D, 0x20].contains($0) }) else {
            return .invalid
        }
        return .complete(Data(buffer[..<newline]))
    }
}
