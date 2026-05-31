import AppKit
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    let queue = ApprovalQueue()
    let server = SocketServer()
    let windowManager = ApprovalWindowManager()

    func start() {
        guard !server.isRunning else { return }
        queue.windowManager = windowManager
        server.start(queue: queue)
    }

    func stop() {
        windowManager.closeAll()
        server.stop()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static var shared: AppState?

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppDelegate.shared?.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppDelegate.shared?.stop()
    }
}

@main
struct OzmApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var state = AppState()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(
                queue: state.queue,
                server: state.server,
                windowManager: state.windowManager
            )
        } label: {
            MenuBarLabel(queue: state.queue)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView(server: state.server)
        }
    }

    init() {
        let state = AppState()
        _state = StateObject(wrappedValue: state)
        AppDelegate.shared = state
    }
}

struct MenuBarLabel: View {
    @ObservedObject var queue: ApprovalQueue

    var body: some View {
        HStack(spacing: 2) {
            Image(systemName: "shield.checkered")
            if queue.pendingCount > 0 {
                Text("\(queue.pendingCount)")
                    .font(.caption2)
                    .monospacedDigit()
            }
        }
    }
}
