import SwiftUI

struct MenuBarView: View {
    @ObservedObject var queue: ApprovalQueue
    @ObservedObject var server: SocketServer
    var windowManager: ApprovalWindowManager? = nil

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if queue.pending.isEmpty {
                emptyState
            } else {
                approvalList
            }
            Divider()
            footer
        }
        .frame(width: 420)
        .onAppear {
            if !server.isRunning {
                server.start(queue: queue)
            }
        }
    }

    private var header: some View {
        HStack {
            Image(systemName: "shield.checkered")
                .foregroundStyle(.secondary)
            Text("ozm")
                .font(.headline)
            Spacer()
            Toggle("DND", isOn: $queue.isDND)
                .toggleStyle(.switch)
                .controlSize(.mini)
                .labelsHidden()
            Text("DND")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "checkmark.shield")
                .font(.title)
                .foregroundStyle(.tertiary)
            Text("No pending approvals")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }

    private var approvalList: some View {
        VStack(spacing: 1) {
            ForEach(queue.pending) { item in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Image(systemName: typeIcon(for: item.request.type))
                            .foregroundStyle(item.request.type == .override ? .red : .blue)
                            .frame(width: 20)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.request.agent.name)
                                .font(.callout.weight(.medium))
                                .lineLimit(1)
                            Text(item.request.payload.command ?? item.request.payload.script ?? item.request.agent.description)
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer()
                    }
                    HStack(spacing: 6) {
                        MenuBarFeedbackField(item: item, queue: queue, windowManager: windowManager)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Color(nsColor: .controlBackgroundColor))
            }
        }
    }

    private func typeIcon(for type: ApprovalRequest.RequestType) -> String {
        switch type {
        case .fileApproval: "doc.text"
        case .cmdApproval: "terminal"
        case .override: "exclamationmark.shield"
        case .status: "info.circle"
        }
    }

    private var footer: some View {
        HStack {
            Circle()
                .fill(server.isRunning ? .green : .red)
                .frame(width: 6, height: 6)
            Text(server.isRunning ? "Listening" : "Stopped")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Text("\(queue.pendingCount) pending")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }
}

struct MenuBarFeedbackField: View {
    let item: ApprovalQueue.PendingApproval
    @ObservedObject var queue: ApprovalQueue
    var windowManager: ApprovalWindowManager?
    @State private var feedback = ""

    var body: some View {
        TextField("Feedback...", text: $feedback)
            .textFieldStyle(.roundedBorder)
            .font(.caption)
            .frame(maxWidth: .infinity)

        Button {
            windowManager?.open(item: item, queue: queue)
        } label: {
            Image(systemName: "arrow.up.forward.square")
        }
        .buttonStyle(.borderless)
        .help("Open in window")

        Button("Deny") {
            queue.respond(to: item.id, with: ApprovalResponse(
                id: item.request.id,
                decision: .deny,
                feedback: feedback.isEmpty ? nil : feedback
            ))
        }
        .controlSize(.small)

        Button(item.request.type == .override ? "Allow Once" : "Allow") {
            queue.respond(to: item.id, with: ApprovalResponse(
                id: item.request.id,
                decision: .allow,
                feedback: feedback.isEmpty ? nil : feedback,
                command: item.request.payload.command
            ))
        }
        .controlSize(.small)
        .buttonStyle(.borderedProminent)
    }
}
