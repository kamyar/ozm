import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let queue = ApprovalQueue()
    let server = SocketServer()

    func applicationDidFinishLaunching(_ notification: Notification) {
        server.start(queue: queue)
    }

    func applicationWillTerminate(_ notification: Notification) {
        server.stop()
    }
}

@main
struct OzmApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(queue: delegate.queue, server: delegate.server)
        } label: {
            HStack(spacing: 2) {
                Image(systemName: "shield.checkered")
                if delegate.queue.pendingCount > 0 {
                    Text("\(delegate.queue.pendingCount)")
                        .font(.caption2)
                        .monospacedDigit()
                }
            }
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView(server: delegate.server)
        }
    }
}
