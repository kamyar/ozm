import Foundation
import Testing
@testable import OzmApp

@Suite @MainActor
struct ApprovalQueueTests {
    private final class Windows: ApprovalWindowPresenting {
        var opened: [UUID] = []
        var closed: [UUID] = []
        var closeAllCount = 0

        func open(item: ApprovalQueue.PendingApproval, queue: ApprovalQueue) {
            opened.append(item.id)
        }

        func close(id: UUID) {
            closed.append(id)
        }

        func closeAll() {
            closeAllCount += 1
        }
    }

    private func request(_ type: ApprovalRequest.RequestType) -> ApprovalRequest {
        ApprovalRequest(
            version: 1,
            id: UUID().uuidString,
            type: type,
            agent: AgentInfo(name: "Tests", description: "Test approval modes."),
            payload: ApprovalRequest.Payload(command: "echo original")
        )
    }

    // Wait until enqueue has run, not until its response has arrived.
    private func enqueue(
        _ request: ApprovalRequest,
        on queue: ApprovalQueue
    ) async -> Task<ApprovalResponse, Never> {
        var task: Task<ApprovalResponse, Never>!
        await withCheckedContinuation { ready in
            task = Task { @MainActor in
                await withCheckedContinuation { response in
                    queue.enqueue(request, continuation: response)
                    ready.resume()
                }
            }
        }
        return task
    }

    @Test func modesDefaultOffWithoutSavedSettings() {
        let queue = ApprovalQueue(defaults: nil)
        #expect(!queue.isHandsFree)
        #expect(!queue.isDND)
    }

    @Test func restoresSavedApprovalModes() throws {
        let domain = "ozm-tests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: domain))
        defer { defaults.removePersistentDomain(forName: domain) }
        let queue = ApprovalQueue(defaults: defaults)
        #expect(!queue.isHandsFree)
        #expect(!queue.isDND)
        queue.isDND = true
        queue.isHandsFree = true

        let restartedDefaults = try #require(UserDefaults(suiteName: domain))
        let restarted = ApprovalQueue(defaults: restartedDefaults)
        #expect(restarted.isHandsFree)
        #expect(restarted.isDND)
        #expect(restarted.pending.isEmpty)
        #expect(defaults.persistentDomain(forName: domain)?[ApprovalQueue.handsFreeKey] as? Bool == true)

        restarted.isHandsFree = false
        restarted.isDND = false
        let disabled = ApprovalQueue(defaults: restartedDefaults)
        #expect(!disabled.isHandsFree)
        #expect(!disabled.isDND)
    }

    @Test func restoredHandsFreeApprovesWithoutWindowsAndKeepsDND() async throws {
        let domain = "ozm-tests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: domain))
        defer { defaults.removePersistentDomain(forName: domain) }
        defaults.set(true, forKey: ApprovalQueue.handsFreeKey)
        defaults.set(true, forKey: ApprovalQueue.dndKey)
        let queue = ApprovalQueue(defaults: defaults)
        let windows = Windows()
        queue.windowManager = windows

        let task = await enqueue(request(.override), on: queue)
        let response = await task.value
        #expect(response.decision == .allow)
        #expect(response.handsFree)
        #expect(queue.isDND)
        #expect(queue.pending.isEmpty)
        #expect(windows.opened.isEmpty)
    }

    @Test func newRequestsAreApprovedWithoutWindowsOrRules() async throws {
        let queue = ApprovalQueue(defaults: nil)
        let windows = Windows()
        queue.windowManager = windows
        queue.isHandsFree = true

        for type in [ApprovalRequest.RequestType.fileApproval, .cmdApproval, .override] {
            let request = request(type)
            let response = await withCheckedContinuation { continuation in
                queue.enqueue(request, continuation: continuation)
            }
            #expect(response.id == request.id)
            #expect(response.decision == .allow)
            #expect(response.command == request.payload.command)
            #expect(response.handsFree)
            #expect(response.feedback == nil)
            #expect(response.allowPattern == nil)
            #expect(response.blockPattern == nil)
            #expect(!response.applyGlobally)
            #expect(queue.pending.isEmpty)
            let data = try JSONEncoder().encode(response)
            let json = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
            #expect(json["hands_free"] as? Bool == true)
        }
        #expect(windows.opened.isEmpty)
    }

    @Test func enablingApprovesAllPendingWithoutOpeningOtherWindows() async {
        let queue = ApprovalQueue(defaults: nil)
        let windows = Windows()
        queue.windowManager = windows
        let first = request(.cmdApproval)
        let second = request(.override)
        let third = request(.fileApproval)
        let firstTask = await enqueue(first, on: queue)
        let secondTask = await enqueue(second, on: queue)
        let thirdTask = await enqueue(third, on: queue)
        #expect(queue.pendingCount == 3)
        #expect(windows.opened == [UUID(uuidString: first.id)!])

        queue.isHandsFree = true

        for task in [firstTask, secondTask, thirdTask] {
            let response = await task.value
            #expect(response.decision == .allow)
            #expect(response.handsFree)
        }
        #expect(queue.pending.isEmpty)
        #expect(windows.closeAllCount == 1)
        #expect(windows.opened.count == 1)
        // Late clicks and disconnects must not resume resolved requests again.
        queue.cancel(id: UUID(uuidString: first.id)!)
        queue.respond(to: UUID(uuidString: second.id)!, with: ApprovalResponse(
            id: second.id, decision: .deny
        ))
        queue.cancelAll()
    }

    @Test func handsFreeApprovesDNDBacklogWithoutChangingDND() async {
        let queue = ApprovalQueue(defaults: nil)
        let windows = Windows()
        queue.windowManager = windows
        queue.isDND = true
        let pendingTask = await enqueue(request(.fileApproval), on: queue)
        #expect(queue.pendingCount == 1)
        #expect(windows.opened.isEmpty)

        queue.isHandsFree = true
        let pendingResponse = await pendingTask.value
        #expect(pendingResponse.handsFree)
        let newTask = await enqueue(request(.override), on: queue)
        let newResponse = await newTask.value
        #expect(newResponse.handsFree)
        #expect(queue.isDND)
        #expect(queue.pending.isEmpty)
        #expect(windows.opened.isEmpty)

        queue.isHandsFree = false
        let manualRequest = request(.cmdApproval)
        let manualTask = await enqueue(manualRequest, on: queue)
        #expect(queue.pendingCount == 1)
        #expect(windows.opened.isEmpty)
        queue.isDND = false
        #expect(windows.opened == [UUID(uuidString: manualRequest.id)!])
        queue.cancelAll()
        let cancelled = await manualTask.value
        #expect(cancelled.decision == .error)
    }

    @Test func disablingRestoresManualReview() async {
        let queue = ApprovalQueue(defaults: nil)
        let windows = Windows()
        queue.windowManager = windows
        queue.isHandsFree = true
        queue.isHandsFree = false
        let request = request(.cmdApproval)
        let task = await enqueue(request, on: queue)
        #expect(queue.pendingCount == 1)
        #expect(windows.opened == [UUID(uuidString: request.id)!])
        queue.respond(to: UUID(uuidString: request.id)!, with: ApprovalResponse(
            id: request.id, decision: .deny
        ))
        let response = await task.value
        #expect(response.decision == .deny)
        #expect(!response.handsFree)
        #expect(queue.pending.isEmpty)
    }
}
