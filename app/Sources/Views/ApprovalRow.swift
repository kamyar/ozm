import SwiftUI

struct ApprovalRow: View {
    let item: ApprovalQueue.PendingApproval
    @ObservedObject var queue: ApprovalQueue
    @State private var isExpanded = false
    @State private var feedback = ""

    private var typeIcon: String {
        switch item.request.type {
        case .fileApproval: "doc.text"
        case .cmdApproval: "terminal"
        case .override: "exclamationmark.shield"
        case .status: "info.circle"
        }
    }

    private var typeLabel: String {
        switch item.request.type {
        case .fileApproval:
            "[\(item.request.payload.label ?? "NEW")] Script"
        case .cmdApproval: "Command"
        case .override: "Override"
        case .status: "Status"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            summaryRow
            if isExpanded {
                expandedContent
            }
        }
        .background(Color(nsColor: .controlBackgroundColor))
    }

    private var summaryRow: some View {
        Button { isExpanded.toggle() } label: {
            HStack(spacing: 8) {
                Image(systemName: typeIcon)
                    .foregroundStyle(item.request.type == .override ? .red : .blue)
                    .frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.request.agent.name)
                        .font(.callout.weight(.medium))
                        .lineLimit(1)
                    Text(item.request.agent.description)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Text(typeLabel)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary)
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var expandedContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch item.request.type {
            case .fileApproval:
                FileApprovalDetail(payload: item.request.payload)
            case .cmdApproval:
                CommandApprovalDetail(payload: item.request.payload)
            case .override:
                OverrideDetail(payload: item.request.payload)
            case .status:
                EmptyView()
            }

            TextField("Feedback for the agent...", text: $feedback)
                .textFieldStyle(.roundedBorder)
                .font(.callout)

            HStack {
                Spacer()
                Button("Deny") { deny() }
                    .keyboardShortcut(.cancelAction)
                Button(item.request.type == .override ? "Allow Once" : "Allow") {
                    allow()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 10)
    }

    private func allow() {
        queue.respond(to: item.id, with: ApprovalResponse(
            id: item.request.id,
            decision: .allow,
            feedback: feedback.isEmpty ? nil : feedback,
            command: item.request.payload.command
        ))
    }

    private func deny() {
        queue.respond(to: item.id, with: ApprovalResponse(
            id: item.request.id,
            decision: .deny,
            feedback: feedback.isEmpty ? nil : feedback
        ))
    }
}

struct FileApprovalDetail: View {
    let payload: ApprovalRequest.Payload

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let script = payload.script {
                Label(script, systemImage: "doc")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            if let lineCount = payload.lineCount {
                Text("\(lineCount) lines")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            ScrollView {
                ColoredCodeView(
                    text: payload.diff ?? payload.content ?? "",
                    isDiff: payload.diff != nil,
                    syntax: payload.syntax
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 200)
            .background(Color(nsColor: .textBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 4))
        }
    }
}

struct CommandApprovalDetail: View {
    let payload: ApprovalRequest.Payload

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Command:")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(payload.command ?? "")
                .font(.system(size: 12, design: .monospaced))
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 4))
                .textSelection(.enabled)
        }
    }
}

struct OverrideDetail: View {
    let payload: ApprovalRequest.Payload

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let command = payload.command {
                Label(command, systemImage: "terminal")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            if let violation = payload.violation {
                HStack(spacing: 4) {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(violation)
                        .font(.callout)
                }
            }
            if let reason = payload.reason {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Agent reasoning:")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(reason)
                        .font(.callout)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                }
            }
        }
    }
}
