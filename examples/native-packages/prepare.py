"""Prepare the image compression and PostHog Swift packages used for Cruvio."""

import argparse
import json
import os
import shutil
from pathlib import Path


def replace(source, original, updated):
    if source.count(original) != 1:
        raise SystemExit(f"Expected exactly one occurrence of {original!r}. Check the documented dependency versions.")
    return source.replace(original, updated, 1)


def patch(path, original, updated):
    path.chmod(path.stat().st_mode | 0o200)
    path.write_text(replace(path.read_text(), original, updated))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="Private project state created by register")
    parser.add_argument("mantle", type=Path, help="Mantle 2.2.0 source checkout")
    parser.add_argument("posthog", type=Path, help="PostHog iOS 3.56.0 source checkout")
    args = parser.parse_args()
    os.umask(0o077)
    examples = Path(__file__).resolve().parent
    state = args.state.resolve()
    native = state / "native-packages"
    packages = state / "source/app/ios/Flutter/ephemeral/Packages"
    manifest = packages / "FlutterGeneratedPluginSwiftPackage/Package.swift"
    pub = Path(os.environ.get("PUB_CACHE", Path.home() / ".pub-cache")) / "hosted/pub.dev"
    image_name = "flutter_image_compress_common"
    posthog_plugin = "posthog_flutter"
    text = manifest.read_text()
    old = '.package(name: "posthog_flutter", path: "../.packages/posthog_flutter-5.24.2"),'
    text = replace(text, old,
                   f'.package(name: "{posthog_plugin}", path: {json.dumps(str(native / posthog_plugin))}),\n'
                   f'        .package(name: "{image_name}", path: {json.dumps(str(native / image_name))}),')
    old = '.product(name: "posthog-flutter", package: "posthog_flutter"),'
    text = replace(text, old, old + '\n                .product(name: "flutter-image-compress-common", '
                   'package: "flutter_image_compress_common"),')

    image = native / image_name
    posthog = native / "posthog-ios"
    plugin = native / posthog_plugin
    mantle = native / "Mantle"
    if not all(directory.is_dir() for directory in (image, posthog, plugin, mantle)):
        if any(directory.exists() for directory in (image, posthog, plugin, mantle)):
            raise SystemExit("The private native packages are incomplete. Move the partial copies aside and retry.")
        shutil.copytree(pub / f"{image_name}-1.0.6/ios/Classes", image / "Classes")
        shutil.copytree(pub / f"{posthog_plugin}-5.24.2/darwin/posthog_flutter", plugin)
        shutil.copytree(args.mantle / "Mantle", mantle / "Mantle")
        shutil.copytree(args.posthog / "PostHog", posthog / "PostHog")
        shutil.copyfile(args.posthog / "LICENSE", posthog / "LICENSE")
        shutil.copyfile(args.mantle / "LICENSE.md", mantle / "LICENSE.md")
        linux_packages = (
            (args.mantle / "Package.swift", mantle),
            (examples / "posthog-ios.Package.swift", posthog),
            (examples / "image-compression.Package.swift", image),
        )
        for source, target in linux_packages:
            shutil.copyfile(source, target / "Package.swift")

        classes = image / "Classes"
        (classes / "module.modulemap").write_text(
            'module flutter_image_compress_common {\n    header "ImageCompressPlugin.h"\n    export *\n}\n')
        patch(classes / "CompressFileHandler.m",
              "#import <SDWebImageWebPCoder/SDWebImageWebPCoder.h>\n#import <SDWebImage/SDWebImage.h>",
              "@import SDWebImageWebPCoder;\n@import SDWebImage;")
        patch(classes / "CompressHandler.m", '#import "UIImage+WebP.h"\n', "")
        patch(classes / "CompressHandler.m", "#import <SDWebImageWebPCoder/SDImageWebPCoder.h>",
              "@import SDWebImageWebPCoder;")
        patch(classes / "CompressHandler.h", "#import <Foundation/Foundation.h>",
              "#import <Foundation/Foundation.h>\n\n@class UIImage;")
        patch(classes / "SYPictureMetadata/SYMetadataBase.h", "#import <Mantle/Mantle.h>", "@import Mantle;")
        patch(classes / "SYPictureMetadata/SYMetadataExif.m", '"SYMetadataEXIF.h"', '"SYMetadataExif.h"')

        webp = posthog / "PostHog/Utils/UIImage+WebP.swift"
        content = webp.read_text()
        start = content.index("    #if canImport(phlibwebp)")
        end = content.index("    import UIKit", start)
        patch(webp, content[start:end], "    @_implementationOnly import libwebp\n")
        patch(plugin / "Package.swift", 'path: "../FlutterFramework"',
              'path: "../../source/app/ios/Flutter/ephemeral/Packages/.packages/FlutterFramework"')
        patch(plugin / "Package.swift", '.package(url: "https://github.com/PostHog/posthog-ios", "3.56.0"..<"4.0.0")',
              '.package(path: "../posthog-ios")')

    manifest.write_text(text)
    print("Prepared native packages. Run resolve-swift before the offline build.")


if __name__ == "__main__":
    main()
