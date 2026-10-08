// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "PixelCrew",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "PixelCrew", path: "Sources/PixelCrew")
    ]
)
