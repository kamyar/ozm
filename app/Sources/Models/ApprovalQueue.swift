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
    @Published var isDND: Bool = false {
        didSet {
            if !isDND {
                presentNextIfNeeded()
            }
        }
    }
    var windowManager: ApprovalWindowPresenting?

    var pendingCount: Int { pending.count }

    func enqueue(
        _ request: ApprovalRequest,
        continuation: CheckedContinuation<ApprovalResponse, Never>
    ) {
        let wasEmpty = pending.isEmpty
        let item = PendingApproval(
            id: UUID(uuidString: request.id) ?? UUID(),
            request: request,
            receivedAt: Date(),
            continuation: continuation
        )
        pending.append(item)
        if wasEmpty {
            presentNextIfNeeded()
        }
    }

    func respond(to id: UUID, with response: ApprovalResponse) {
        guard let index = pending.firstIndex(where: { $0.id == id }) else { return }
        let wasPresented = index == pending.startIndex
        let item = pending.remove(at: index)
        if wasPresented {
            windowManager?.close(id: id)
        }
        item.continuation.resume(returning: response)
        if wasPresented {
            presentNextIfNeeded()
        }
    }

    func cancel(id: UUID) {
        guard let index = pending.firstIndex(where: { $0.id == id }) else { return }
        let wasActive = index == pending.startIndex
        let item = pending.remove(at: index)
        if wasActive {
            windowManager?.close(id: id)
        }
        item.continuation.resume(returning: ApprovalResponse(
            id: item.request.id,
            decision: .error,
            feedback: "connection closed"
        ))
        if wasActive {
            presentNextIfNeeded()
        }
    }

    func cancelAll() {
        windowManager?.closeAll()
        for item in pending {
            item.continuation.resume(returning: ApprovalResponse(
                id: item.request.id,
                decision: .error,
                feedback: "app shutting down"
            ))
        }
        pending.removeAll()
    }

    private func presentNextIfNeeded() {
        guard !isDND, let next = pending.first else { return }
        windowManager?.open(item: next, queue: self)
    }
}

@MainActor
protocol ApprovalWindowPresenting: AnyObject {
    func open(item: ApprovalQueue.PendingApproval, queue: ApprovalQueue)
    func close(id: UUID)
    func closeAll()
}
