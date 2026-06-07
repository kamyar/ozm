import Foundation
import Network
import SwiftUI

@MainActor
final class SocketServer: ObservableObject {
    @Published var isRunning = false

    private var listener: NWListener?
    private var queue: ApprovalQueue?
    private var connections: [UUID: NWConnection] = [:]

    static let socketPath: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/.ozm/ozm.sock"
    }()

    func start(queue: ApprovalQueue) {
        self.queue = queue
        cleanup()

        let dir = (Self.socketPath as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(
            atPath: dir, withIntermediateDirectories: true
        )

        let params = NWParameters()
        params.defaultProtocolStack.transportProtocol = NWProtocolTCP.Options()
        params.requiredLocalEndpoint = NWEndpoint.unix(path: Self.socketPath)

        do {
            let listener = try NWListener(using: params)
            listener.stateUpdateHandler = { [weak self] state in
                Task { @MainActor [weak self] in
                    switch state {
                    case .ready:
                        self?.isRunning = true
                    case .failed, .cancelled:
                        self?.isRunning = false
                    default:
                        break
                    }
                }
            }
            listener.newConnectionHandler = { [weak self] conn in
                Task { @MainActor [weak self] in
                    self?.handleConnection(conn)
                }
            }
            listener.start(queue: .main)
            self.listener = listener
        } catch {
            print("ozm: failed to start socket server: \(error)")
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
        isRunning = false
        for conn in connections.values {
            conn.cancel()
        }
        connections.removeAll()
        queue?.cancelAll()
        cleanup()
    }

    private func cleanup() {
        unlink(Self.socketPath)
    }

    private func handleConnection(_ connection: NWConnection) {
        let connID = UUID()
        connections[connID] = connection

        connection.stateUpdateHandler = { [weak self] state in
            if case .failed = state {
                Task { @MainActor [weak self] in
                    self?.removeConnection(connID)
                }
            }
        }

        connection.start(queue: .main)
        readRequest(from: connection, id: connID)
    }

    private func readRequest(from connection: NWConnection, id connID: UUID) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1_048_576) {
            [weak self] data, _, isComplete, error in
            Task { @MainActor [weak self] in
                guard let self else { return }

                if let error {
                    print("ozm: connection read error: \(error)")
                    self.removeConnection(connID)
                    return
                }

                guard let data, !data.isEmpty else {
                    self.removeConnection(connID)
                    return
                }

                guard let json = String(data: data, encoding: .utf8),
                      let jsonData = json.trimmingCharacters(in: .whitespacesAndNewlines)
                          .data(using: .utf8) else {
                    self.sendError(on: connection, id: "unknown", connID: connID)
                    return
                }

                let request: ApprovalRequest
                do {
                    request = try JSONDecoder().decode(ApprovalRequest.self, from: jsonData)
                } catch {
                    print("ozm: failed to decode request: \(error)")
                    self.sendError(on: connection, id: "unknown", connID: connID)
                    return
                }

                if request.type == .status {
                    self.handleStatus(request: request, connection: connection, connID: connID)
                    return
                }

                guard let queue = self.queue else {
                    self.sendError(on: connection, id: request.id, connID: connID)
                    return
                }

                let response = await withCheckedContinuation { continuation in
                    queue.enqueue(request, continuation: continuation)
                }

                self.sendResponse(response, on: connection, connID: connID)
            }
        }
    }

    private func handleStatus(
        request: ApprovalRequest,
        connection: NWConnection,
        connID: UUID
    ) {
        let agents = Set((queue?.pending ?? []).map { $0.request.agent.name })
        let statusJSON: [String: Any] = [
            "pending_count": queue?.pendingCount ?? 0,
            "agents": Array(agents),
            "dnd": queue?.isDND ?? false,
        ]

        var response = ApprovalResponse(id: request.id, decision: .status)
        if let data = try? JSONSerialization.data(withJSONObject: statusJSON),
           let str = String(data: data, encoding: .utf8) {
            response.feedback = str
        }
        sendResponse(response, on: connection, connID: connID)
    }

    private func sendResponse(
        _ response: ApprovalResponse,
        on connection: NWConnection,
        connID: UUID
    ) {
        guard let data = try? JSONEncoder().encode(response) else {
            removeConnection(connID)
            return
        }
        var payload = data
        payload.append(contentsOf: "\n".utf8)
        connection.send(content: payload, completion: .contentProcessed { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.removeConnection(connID)
            }
        })
    }

    private func sendError(on connection: NWConnection, id: String, connID: UUID) {
        let response = ApprovalResponse(id: id, decision: .error, feedback: "parse error")
        sendResponse(response, on: connection, connID: connID)
    }

    private func removeConnection(_ id: UUID) {
        connections[id]?.cancel()
        connections.removeValue(forKey: id)
    }
}
