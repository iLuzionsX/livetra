// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "LiveTR3Mac",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "GLM2Scribe", targets: ["LiveTR3Mac"])
    ],
    targets: [
        .executableTarget(
            name: "LiveTR3Mac"
        ),
        .testTarget(
            name: "LiveTR3MacTests",
            dependencies: ["LiveTR3Mac"]
        )
    ]
)
