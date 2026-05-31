import Foundation
import SwiftUI

@MainActor
final class ApprovalQueue: ObservableObject {
    struct PendingApproval: Identifiable {
        let id: UUID
        let request: ApprovalRequest
        let receivedAt: Date
        let continuation: CheckedContinuation<ApprovalResponse, Never>
    }

    @Published var pending: [PendingApproval] = []
    @Published var isDND: Bool = false
    var windowManager: ApprovalWindowManager?

    var pendingCount: Int { pending.count }

    func enqueue(
        _ request: ApprovalRequest,
        continuation: CheckedContinuation<ApprovalResponse, Never>
    ) {
        let item = PendingApproval(
            id: UUID(uuidString: request.id) ?? UUID(),
            request: request,
            receivedAt: Date(),
            continuation: continuation
        )
        pending.append(item)
        if !isDND {
            windowManager?.open(item: item, queue: self)
        }
    }

    func respond(to id: UUID, with response: ApprovalResponse) {
        guard let index = pending.firstIndex(where: { $0.id == id }) else { return }
        let item = pending.remove(at: index)
        item.continuation.resume(returning: response)
        windowManager?.close(id: id)
    }

    func cancel(id: UUID) {
        guard let index = pending.firstIndex(where: { $0.id == id }) else { return }
        let item = pending.remove(at: index)
        item.continuation.resume(returning: ApprovalResponse(
            id: item.request.id,
            decision: .error,
            feedback: "connection closed"
        ))
    }

    func cancelAll() {
        for item in pending {
            item.continuation.resume(returning: ApprovalResponse(
                id: item.request.id,
                decision: .error,
                feedback: "app shutting down"
            ))
        }
        pending.removeAll()
    }
}
