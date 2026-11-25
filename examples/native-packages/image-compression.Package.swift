// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "flutter_image_compress_common",
    platforms: [.iOS(.v15)],
    products: [.library(name: "flutter-image-compress-common", targets: ["flutter_image_compress_common"])],
    dependencies: [
        .package(path: "../../source/app/ios/Flutter/ephemeral/Packages/.packages/FlutterFramework"),
        .package(path: "../Mantle"),
        .package(url: "https://github.com/SDWebImage/SDWebImage.git", exact: "5.21.7"),
        .package(url: "https://github.com/SDWebImage/SDWebImageWebPCoder.git", exact: "0.15.0"),
    ],
    targets: [
        .target(
            name: "flutter_image_compress_common",
            dependencies: [
                .product(name: "FlutterFramework", package: "FlutterFramework"),
                .product(name: "Mantle", package: "Mantle"),
                .product(name: "SDWebImage", package: "SDWebImage"),
                .product(name: "SDWebImageWebPCoder", package: "SDWebImageWebPCoder"),
            ],
            path: "Classes",
            publicHeadersPath: ".",
            cSettings: [.headerSearchPath("SYPictureMetadata")]
        ),
    ]
)
