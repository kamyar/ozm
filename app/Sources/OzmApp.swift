import AppKit
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    let queue = ApprovalQueue()
    let server = SocketServer()
    let windowManager = ApprovalWindowManager()
    private var menuBarController: MenuBarController?

    func start() {
        guard menuBarController == nil else { return }
        queue.windowManager = windowManager
        server.start(queue: queue)
        menuBarController = MenuBarController(
            queue: queue,
            server: server,
            windowManager: windowManager
        )
    }

    func stop() {
        menuBarController?.stop()
        menuBarController = nil
        windowManager.closeAll()
        server.stop()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    static var shared: AppState?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Start as a menu-bar app. ApprovalWindowManager temporarily uses the
        // regular policy while an approval is visible so Command-Tab can find it.
        NSApp.setActivationPolicy(.accessory)
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
