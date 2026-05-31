import AppKit
import SwiftUI

@MainActor
final class ApprovalWindowManager: ObservableObject {
    private var windows: [UUID: NSWindow] = [:]

    func open(item: ApprovalQueue.PendingApproval, queue: ApprovalQueue) {
        if let existing = windows[item.id] {
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate()
            return
        }

        let view = ApprovalWindowContent(item: item, queue: queue, onDismiss: { [weak self] in
            self?.close(id: item.id)
        })

        let hostingView = NSHostingView(rootView: view)
        hostingView.frame = NSRect(x: 0, y: 0, width: 560, height: 480)

        let window = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 560, height: 480),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        window.title = "ozm — \(item.request.agent.name)"
        window.contentView = hostingView
        window.isFloatingPanel = true
        window.level = .floating
        window.center()
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()

        windows[item.id] = window
    }

    func close(id: UUID) {
        windows[id]?.close()
        windows.removeValue(forKey: id)
    }

    func closeAll() {
        for window in windows.values {
            window.close()
        }
        windows.removeAll()
    }
}

struct ApprovalWindowContent: View {
    let item: ApprovalQueue.PendingApproval
    @ObservedObject var queue: ApprovalQueue
    let onDismiss: () -> Void
    @State private var feedback = ""
    @State private var editedCommand: String = ""
    @State private var allowPattern = ""
    @State private var applyGlobally = false

    var body: some View {
        VStack(spacing: 0) {
            agentHeader
            Divider()
            content
            Divider()
            actionBar
        }
        .frame(minWidth: 480, minHeight: 300)
        .onAppear {
            editedCommand = item.request.payload.command ?? ""
        }
    }

    private var agentHeader: some View {
        HStack(spacing: 8) {
            Image(systemName: typeIcon)
                .font(.title2)
                .foregroundStyle(item.request.type == .override ? .red : .blue)
            VStack(alignment: .leading, spacing: 2) {
                Text(typeTitle)
                    .font(.headline)
                Text(item.request.agent.description)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding()
    }

    private var typeIcon: String {
        switch item.request.type {
        case .fileApproval: "doc.text.magnifyingglass"
        case .cmdApproval: "terminal"
        case .override: "exclamationmark.shield"
        case .status: "info.circle"
        }
    }

    private var typeTitle: String {
        switch item.request.type {
        case .fileApproval:
            "[\(item.request.payload.label ?? "NEW")] \(item.request.agent.name)"
        case .cmdApproval:
            "Command — \(item.request.agent.name)"
        case .override:
            "Override — \(item.request.agent.name)"
        case .status:
            "Status"
        }
    }

    @ViewBuilder
    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                switch item.request.type {
                case .fileApproval:
                    fileContent
                case .cmdApproval:
                    cmdContent
                case .override:
                    overrideContent
                case .status:
                    EmptyView()
                }
            }
            .padding()
        }
    }

    private var fileContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let script = item.request.payload.script {
                HStack {
                    Image(systemName: "doc")
                    Text(script)
                        .font(.system(.caption, design: .monospaced))
                    Spacer()
                    if let lineCount = item.request.payload.lineCount {
                        Text("\(lineCount) lines")
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                }
                .foregroundStyle(.secondary)
            }

            let displayText = item.request.payload.diff ?? item.request.payload.content ?? ""
            Text(displayText)
                .font(.system(size: 11, design: .monospaced))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .textSelection(.enabled)
        }
    }

    private var cmdContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Command:")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextEditor(text: $editedCommand)
                .font(.system(size: 12, design: .monospaced))
                .frame(minHeight: 40, maxHeight: 80)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.secondary.opacity(0.3))
                )

            Text("Rule pattern (optional):")
                .font(.caption)
                .foregroundStyle(.secondary)
            TextField("e.g. curl httpbin.org/*", text: $allowPattern)
                .font(.system(size: 12, design: .monospaced))
                .textFieldStyle(.roundedBorder)
            Toggle("Apply globally", isOn: $applyGlobally)
                .font(.callout)
        }
    }

    private var overrideContent: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let command = item.request.payload.command {
                HStack {
                    Image(systemName: "terminal")
                    Text(command)
                        .font(.system(.caption, design: .monospaced))
                }
                .foregroundStyle(.secondary)
            }

            if let violation = item.request.payload.violation {
                HStack(spacing: 6) {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.red)
                    Text(violation)
                        .font(.callout.weight(.medium))
                }
            }

            if let reason = item.request.payload.reason {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Agent reasoning:")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(reason)
                        .font(.callout)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color(nsColor: .textBackgroundColor))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
            }
        }
    }

    private var actionBar: some View {
        HStack {
            TextField("Feedback for the agent...", text: $feedback)
                .textFieldStyle(.roundedBorder)
                .font(.callout)

            Button("Deny") { deny() }
                .keyboardShortcut(.cancelAction)

            Button(item.request.type == .override ? "Allow Once" : "Allow") { allow() }
                .keyboardShortcut(.defaultAction)
        }
        .padding()
    }

    private func allow() {
        let pattern = allowPattern.isEmpty ? nil : allowPattern
        queue.respond(to: item.id, with: ApprovalResponse(
            id: item.request.id,
            decision: .allow,
            feedback: feedback.isEmpty ? nil : feedback,
            command: item.request.type == .cmdApproval ? editedCommand : nil,
            allowPattern: pattern,
            applyGlobally: applyGlobally
        ))
        onDismiss()
    }

    private func deny() {
        let pattern = allowPattern.isEmpty ? nil : allowPattern
        queue.respond(to: item.id, with: ApprovalResponse(
            id: item.request.id,
            decision: .deny,
            feedback: feedback.isEmpty ? nil : feedback,
            blockPattern: pattern,
            applyGlobally: applyGlobally
        ))
        onDismiss()
    }
}
