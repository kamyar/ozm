// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OzmApp",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "OzmApp",
            path: "Sources"
        ),
    ]
)
