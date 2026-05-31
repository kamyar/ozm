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
        ScrollView {
            LazyVStack(spacing: 1) {
                ForEach(queue.pending) { item in
                    HStack(spacing: 0) {
                        ApprovalRow(item: item, queue: queue)
                        Button {
                            windowManager?.open(item: item, queue: queue)
                        } label: {
                            Image(systemName: "arrow.up.forward.square")
                                .font(.callout)
                        }
                        .buttonStyle(.plain)
                        .padding(.trailing, 8)
                        .help("Open in window")
                    }
                }
            }
        }
        .frame(maxHeight: 400)
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
