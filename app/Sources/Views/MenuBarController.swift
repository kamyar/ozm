import AppKit
import Combine
import SwiftUI

@MainActor
final class MenuBarPopover: NSPopover {
    private let hostingController: NSHostingController<MenuBarView>

    init(queue: ApprovalQueue, server: SocketServer, windowManager: ApprovalWindowManager? = nil) {
        hostingController = NSHostingController(rootView: MenuBarView(
            queue: queue,
            server: server,
            windowManager: windowManager
        ))
        super.init()
        behavior = .transient
        animates = false
        // Own the popup size explicitly instead of accepting the hosting
        // window's proposed height, which can leave a large empty backdrop.
        hostingController.sizingOptions = []
        contentViewController = hostingController
        resizeToFit()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not supported")
    }

    func resizeToFit() {
        let size = hostingController.sizeThatFits(in: NSSize(
            width: 420,
            height: CGFloat.greatestFiniteMagnitude
        ))
        hostingController.view.setFrameSize(size)
        contentSize = size
    }
}

@MainActor
final class MenuBarController: NSObject {
    private let queue: ApprovalQueue
    private let statusItem: NSStatusItem
    private let popover: MenuBarPopover
    private var changes: AnyCancellable?

    init(queue: ApprovalQueue, server: SocketServer, windowManager: ApprovalWindowManager) {
        self.queue = queue
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        popover = MenuBarPopover(queue: queue, server: server, windowManager: windowManager)
        super.init()
        statusItem.button?.target = self
        statusItem.button?.action = #selector(togglePopover)
        // objectWillChange fires before the property changes. Refresh on the
        // next main-actor turn so the icon and size use the new state.
        changes = queue.objectWillChange.sink { [weak self] in
            Task { @MainActor [weak self] in self?.refresh() }
        }
        refresh()
    }

    private func refresh() {
        guard let button = statusItem.button else { return }
        button.image = MenuBarIcon.image(handsFree: queue.isHandsFree)
        button.title = queue.pendingCount > 0 ? "\(queue.pendingCount)" : ""
        button.imagePosition = queue.pendingCount > 0 ? .imageLeading : .imageOnly
        button.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        button.toolTip = queue.isHandsFree ? "Ozm — Hands-free is on" : "Ozm"
        button.setAccessibilityLabel(button.toolTip)
        popover.resizeToFit()
    }

    @objc private func togglePopover() {
        if popover.isShown {
            popover.performClose(nil)
        } else if let button = statusItem.button {
            refresh()
            NSApp.activate()
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    func stop() {
        changes?.cancel()
        popover.performClose(nil)
        NSStatusBar.system.removeStatusItem(statusItem)
    }
}
