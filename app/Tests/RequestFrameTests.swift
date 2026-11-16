import Foundation
import Testing
@testable import OzmApp

@Suite
struct RequestFrameTests {
    private func requestData(content: String) throws -> Data {
        let request = ApprovalRequest(
            version: 1,
            id: UUID().uuidString,
            type: .fileApproval,
            agent: AgentInfo(name: "Framing tests", description: "Review synthetic content."),
            payload: ApprovalRequest.Payload(script: "synthetic.sh", label: "NEW", content: content)
        )
        var data = try JSONEncoder().encode(request)
        data.append(0x0A)
        return data
    }

    @Test @MainActor func fragmentedFileRequestsReachHandsFreeQueue() async throws {
        let data = try requestData(content: String(repeating: "x", count: 262_144))
        for chunkSize in [7, 8192, 65_536] {
            var frame = RequestFrame()
            var decoded: ApprovalRequest?
            for offset in stride(from: 0, to: data.count, by: chunkSize) {
                let end = min(offset + chunkSize, data.count)
                let state = frame.append(data.subdata(in: offset..<end))
                switch state {
                case .waiting:
                    #expect(end < data.count)
                case .invalid:
                    Issue.record("A valid fragmented request was rejected.")
                case .complete(let message):
                    #expect(end == data.count)
                    decoded = try JSONDecoder().decode(ApprovalRequest.self, from: message)
                }
            }
            let request = try #require(decoded)
            #expect(request.payload.content?.utf8.count == 262_144)
            let queue = ApprovalQueue(defaults: nil)
            queue.isHandsFree = true
            let response = await withCheckedContinuation { continuation in
                queue.enqueue(request, continuation: continuation)
            }
            #expect(response.id == request.id)
            #expect(response.decision == .allow)
            #expect(response.handsFree)
            #expect(queue.pending.isEmpty)
        }
    }

    @Test func utf8AndEscapedNewlinesCanSplitAtAnyByte() throws {
        let content = "First line\nUnicode: € 🙂\nLast line"
        let data = try requestData(content: content)
        var frame = RequestFrame()
        var decoded: ApprovalRequest?
        for byte in data {
            switch frame.append(Data([byte])) {
            case .waiting: break
            case .invalid: Issue.record("Split UTF-8 was rejected.")
            case .complete(let message):
                decoded = try JSONDecoder().decode(ApprovalRequest.self, from: message)
            }
        }
        #expect(decoded?.payload.content == content)
    }

    @Test func truncatedRequestIsInvalidAtEndOfStream() {
        var frame = RequestFrame()
        guard case .invalid = frame.append(Data("{\"version\":".utf8), endOfStream: true) else {
            Issue.record("Truncated JSON must not be accepted.")
            return
        }
    }

    @Test func acceptsOneRequestWithTrailingWhitespace() {
        var frame = RequestFrame()
        guard case .complete(let message) = frame.append(Data("{}\n \r\n\t".utf8)) else {
            Issue.record("Trailing whitespace should be accepted.")
            return
        }
        #expect(message == Data("{}".utf8))
    }

    @Test func rejectsMultipleRequestsInOneFrame() {
        var frame = RequestFrame()
        guard case .invalid = frame.append(Data("{}\n{}\n".utf8)) else {
            Issue.record("Only one request is allowed per connection.")
            return
        }
    }

    @Test func boundsTheTotalSizeAcrossChunks() {
        var frame = RequestFrame()
        let prefix = Data(repeating: 0x78, count: RequestFrame.maximumBytes - 1)
        guard case .waiting = frame.append(prefix),
              case .complete(let message) = frame.append(Data([0x0A])) else {
            Issue.record("A frame at the size limit should be accepted.")
            return
        }
        #expect(message.count == prefix.count)

        var oversized = RequestFrame()
        guard case .waiting = oversized.append(prefix),
              case .invalid = oversized.append(Data([0x78, 0x0A])) else {
            Issue.record("A frame over the total size limit must be rejected.")
            return
        }
    }
}
