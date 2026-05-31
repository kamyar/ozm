import SwiftUI
import ServiceManagement

struct SettingsView: View {
    @ObservedObject var server: SocketServer
    @State private var launchAtLogin = SMAppService.mainApp.status == .enabled

    var body: some View {
        Form {
            Section("Server") {
                HStack {
                    Circle()
                        .fill(server.isRunning ? .green : .red)
                        .frame(width: 8, height: 8)
                    Text(server.isRunning ? "Running" : "Stopped")
                }
                Text(SocketServer.socketPath)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            Section("Startup") {
                Toggle("Launch at login", isOn: $launchAtLogin)
                    .onChange(of: launchAtLogin) { _, newValue in
                        do {
                            if newValue {
                                try SMAppService.mainApp.register()
                            } else {
                                try SMAppService.mainApp.unregister()
                            }
                        } catch {
                            launchAtLogin = SMAppService.mainApp.status == .enabled
                        }
                    }
            }
        }
        .formStyle(.grouped)
        .frame(width: 360, height: 200)
    }
}
