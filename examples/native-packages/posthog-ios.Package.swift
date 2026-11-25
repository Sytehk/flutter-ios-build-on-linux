// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PostHog",
    platforms: [.iOS(.v15)],
    products: [.library(name: "PostHog", targets: ["PostHog"])],
    dependencies: [
        .package(url: "https://github.com/microsoft/plcrashreporter.git", exact: "1.12.2"),
        .package(url: "https://github.com/SDWebImage/libwebp-Xcode.git", exact: "1.6.0"),
    ],
    targets: [
        .target(
            name: "PostHog",
            dependencies: [
                .product(name: "CrashReporter", package: "plcrashreporter"),
                .product(name: "libwebp", package: "libwebp-Xcode"),
            ],
            path: "PostHog",
            resources: [.copy("Resources/PrivacyInfo.xcprivacy")]
        ),
    ]
)
