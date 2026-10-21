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
    // Session-only. Never restore automatic approval after an app restart.
    @Published var isHandsFree: Bool = false {
        didSet {
            if isHandsFree {
                approveAllPending()
            }
        }
    }
    var windowManager: ApprovalWindowPresenting?

    var pendingCount: Int { pending.count }

    func enqueue(
        _ request: ApprovalRequest,
        continuation: CheckedContinuation<ApprovalResponse, Never>
    ) {
        if isHandsFree {
            continuation.resume(returning: handsFreeResponse(for: request))
            return
        }
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

    private func handsFreeResponse(for request: ApprovalRequest) -> ApprovalResponse {
        ApprovalResponse(
            id: request.id,
            decision: .allow,
            command: request.payload.command,
            handsFree: true
        )
    }

    private func approveAllPending() {
        // Drain together so resolving one request cannot open the next window.
        let items = pending
        pending.removeAll()
        windowManager?.closeAll()
        for item in items {
            item.continuation.resume(returning: handsFreeResponse(for: item.request))
        }
    }

    private func presentNextIfNeeded() {
        guard !isHandsFree, !isDND, let next = pending.first else { return }
        windowManager?.open(item: next, queue: self)
    }
}

@MainActor
protocol ApprovalWindowPresenting: AnyObject {
    func open(item: ApprovalQueue.PendingApproval, queue: ApprovalQueue)
    func close(id: UUID)
    func closeAll()
}
