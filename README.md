# Build and run Flutter iOS apps on Linux

This guide builds, signs, and runs a stock Flutter app on a physical iPhone using SweetPad on Linux. Complete sections 1–3, then follow the [stock app walkthrough](#stock-app-walkthrough). The later sections cover additional native plugins, app entitlements, and Debug hot reload.

The app source stays in its own checkout. Docker mounts the source read-only and writes generated files and build output to a private workspace. Compilation and signing run with Docker networking disabled. Installation goes directly to the iPhone over USB. Apple is still involved in downloading the SDK, issuing signing assets, and, when needed, personalizing developer support for the phone.

## Versions used for the verified setup

| Component | Version or location |
| --- | --- |
| Linux workstation | Ubuntu 26.04.1 LTS, x86-64 |
| Shared SweetPad workspace | `~/sweetpad-ios` |
| Flutter and Dart | Flutter 3.50.0, Dart 3.14.0 |
| Flutter engine revision | `371f77202a73e8dff57a8b3bff75bca55c090aee` |
| SweetPad CLI and local Docker image | 0.1.29, `sweetpad-local:0.1.29` |
| Apple SDK | iPhoneOS 27.2, imported from Apple's Xcode 27.2 archive |
| Swift and Swift Build | 6.4.2 and 6.4.0 |
| CocoaPods | 1.16.2, for projects with CocoaPods dependencies |
| Signing and USB | `rcodesign` 0.29.0 and `pymobiledevice3` 11.50.0 |

SweetPad's Linux support is experimental. The combination above ran on this machine. Treat a different Flutter engine, Xcode SDK, or set of native plugins as a compatibility change. [SweetPad's Linux support guide](https://sweetpad.hyzyla.dev/docs/cli/linux/)

## 1. Prepare the Linux host

Have Docker running with permission to use it from your shell, Git, Python 3 with `venv`, OpenSSL, `curl`, `xz`, `usbmuxd`, and a data-capable USB cable. On Ubuntu or Debian, install missing host packages with your package manager. The package names are `git python3 python3-venv openssl curl xz-utils unzip usbmuxd`. Keep the SDK, Xcode extraction, compiler sources, and DerivedData on local storage with room for several gigabytes of build state.

Clone this repository to a private folder and use that checkout as the shared workspace. The commands use `~/sweetpad-ios` as an example. Set `tools` to the full path of your checkout. Keep the Flutter app checkout in a separate folder.

```bash
umask 077
tools="$HOME/sweetpad-ios"  # Path to this repository's checkout
flutter_sdk="$HOME/development/flutter"
mkdir -p "$tools"/{tools,containers,docker-config,signing-tools,projects}
docker version
systemctl is-active usbmuxd
```

Install the exact Flutter version used by the guide. For an existing Flutter 3.50.0 installation, set `flutter_sdk` to its path and run the version check. If you choose a separate `PUB_CACHE`, export it before the first Flutter invocation so the Flutter tool's own dependencies and the app's dependencies use the same cache.

```bash
mkdir -p "$(dirname "$flutter_sdk")"
git clone --depth 1 --branch 3.50.0 https://github.com/flutter/flutter.git "$flutter_sdk"
export SWEETPAD_FLUTTER_SDK="$flutter_sdk"
"$flutter_sdk/bin/flutter" --version
```

The local runner defaults to `~/development/flutter` and `~/.pub-cache`. Keep `SWEETPAD_FLUTTER_SDK` and `PUB_CACHE` consistent throughout preparation and build. [Flutter SDK archive](https://docs.flutter.dev/install/archive)

## 2. Install SweetPad and import the Apple SDK

1. Download the [SweetPad 0.1.29 Linux release](https://github.com/sweetpad-dev/sweetpad/releases/tag/cli-v0.1.29) archive and its checksum for the **host** architecture. We used `sweetpad-cli-0.1.29-linux-x64.tar.gz`. Check the checksum and extract the whole archive into `$tools/tools`, including `libexec`.
2. Download `Xcode_27.2.xip` directly through Apple Developer Downloads. We supplied Apple's archive to SweetPad on Linux. A Mac-exported SweetPad SDK archive is also supported.
3. Pull the SweetPad Docker image. Import the Apple archive with a local Docker process after the image is available. This puts the extracted SDK at `$tools/darwin.artifactbundle`.

```bash
archive="$HOME/Downloads/sweetpad-cli-0.1.29-linux-x64.tar.gz"
(cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256")
tar -xzf "$archive" -C "$tools/tools"
"$tools/tools/sweetpad" --version

docker pull ghcr.io/sweetpad-dev/sweetpad:0.1.29
xcode_archive="$HOME/Downloads/Xcode_27.2.xip"
docker run --rm --network none \
  --entrypoint /usr/local/bin/sweetpad \
  --mount "type=bind,source=$xcode_archive,target=/input/Xcode_27.2.xip,readonly" \
  --mount "type=bind,source=$tools,target=/workspace" \
  ghcr.io/sweetpad-dev/sweetpad:0.1.29 \
  sdk import /input/Xcode_27.2.xip \
  --destination /workspace/darwin.artifactbundle --non-interactive
```

Use the actual path to the downloaded archive. The host alternative is `sweetpad setup --sdk /path/to/Xcode_27.2.xip --sdk-path "$tools/darwin.artifactbundle"`. The Docker recipe uses the pinned compiler environment in the base image. Keep the Apple SDK archive and extracted SDK private. [SweetPad's SDK setup instructions](https://sweetpad.hyzyla.dev/docs/cli/linux/sdk-transfer/) · [Apple Developer Downloads](https://developer.apple.com/download/applications/)

Build the included [Dockerfile](containers/Dockerfile) for CocoaPods and the LLVM Mach-O utilities used by the packaging steps. The tool links in `apple-shims/` and `sweetpad-native-tools/` resolve after the CLI archive is unpacked under `tools/`.

```bash
docker build -t sweetpad-local:0.1.29 "$tools/containers"
```

Image setup and dependency downloads happen before the app build.

## 3. Prepare Flutter's iPhone Release compiler

A Flutter Release app needs a Dart AOT compiler that runs on Linux and targets **arm64 iOS**. The iOS `gen_snapshot_arm64` from the stock Flutter cache is a macOS executable. The working setup uses a Linux executable built from the Dart revision pinned by the app's Flutter engine.

First populate the official iOS Flutter framework artifacts and take a private copy of the Flutter cache for the Docker runner.

```bash
"$flutter_sdk/bin/flutter" precache --ios

# Use an empty destination for a new workspace.
mkdir -p "$tools/flutter-cache"
cp -a "$flutter_sdk/bin/cache/." "$tools/flutter-cache/"
```

If a matching prebuilt Linux iOS compiler is available, verify its release manifest and checksum. For Flutter 3.50.0 we built the compiler locally with `xcross_gen_snapshot`.

```bash
git clone https://github.com/arxdeus/xcross_gen_snapshot.git "$tools/aot-compiler-source"
git -C "$tools/aot-compiler-source" checkout --detach f859163ffbe9707c0aedbb9b566b17ecf9aa139a
engine="$(cat "$flutter_sdk/bin/internal/engine.version")"

(
  cd "$tools/aot-compiler-source"
  export PATH="$flutter_sdk/bin:$PATH"
  export PUB_CACHE="$tools/aot-pub-cache"
  dart pub get
  dart run xcross_gen_snapshot:build \
    --engine "$engine" \
    --mode release \
    --out "$tools/aot-compiler" \
    --work "$tools/aot-compiler-work" \
    --jobs 5
)

file "$tools/aot-compiler/gen_snapshot"
"$tools/aot-compiler/gen_snapshot" --version
```

The expected host binary is a Linux ELF executable. Check the engine recorded in `$tools/aot-compiler/build.json` against the Flutter SDK's `bin/internal/engine.version`. The build runner mounts this compiler over the cached iOS Release snapshot executable inside Docker. The host Flutter SDK is mounted read-only. [Compiler source and release manifests](https://github.com/arxdeus/xcross_gen_snapshot)

## 4. Set up the local runners and register an app

This repository supplies the shared runner and compatibility scripts. The upstream CLI download supplies SweetPad itself. Each project gets its own build state, settings, and signing files under `projects/`, which Git ignores.

### SweetPad workspace layout

The tree shows the shared tools and one example registered app, `my-app`.
The Flutter checkout lives separately at the path saved in its project profile.
Folders for build products, logs, and caches appear as the relevant steps run.

```text
~/sweetpad-ios/
├── tools/                              SweetPad CLI, libexec, and bundled docs
├── containers/
│   └── Dockerfile                      Local build image recipe
├── docker-config/                      Private Docker CLI configuration
├── darwin.artifactbundle/              Imported Apple SDK and tools
├── flutter-cache/                      Private Flutter SDK cache and iOS artifacts
├── aot-compiler/
│   ├── gen_snapshot                    Linux compiler targeting iOS arm64 Release
│   └── build.json                      Compiler and Flutter engine metadata
├── aot-compiler-source/                Source checkout for rebuilding the compiler
├── aot-compiler-work/                  Compiler build intermediates
├── aot-pub-cache/                      Dart package cache for the compiler build
├── apple-shims/                        Apple SDK and Mach-O tool compatibility
├── sweetpad-native-tools/              Native build helper tools
├── prepare_ios_plugins.dart            Generates the Flutter iOS plugin package
├── device-tools/                       Python environment with pymobiledevice3
├── debugger-tools/                     Private Linux debugger
│   └── root/usr/                       LLDB 21 and its runtime libraries
├── signing-tools/
│   └── rcodesign                       Offline signing binary
├── shared-flutter.lock                 Coordinates shared tool and cache use
├── shared-ios/
│   ├── README.md
│   ├── sweetpad-ios.py                  Registers, prepares, builds, and installs
│   ├── container-build.sh              Runs the build inside Docker
│   └── sign-app.sh                      Signs a staged bundle inside Docker
└── projects/
    ├── my-app.json                     Checkout path, inputs, and overrides
    └── my-app/
        ├── source/app/                 Private build tree with checkout inputs mounted
        │   ├── .dart_tool/             Generated Dart and Flutter build state
        │   ├── build/                  Generated Flutter output
        │   └── ios/Flutter/
        │       ├── Generated.xcconfig
        │       └── ephemeral/          Generated Flutter and plugin files
        ├── derived-data/
        │   └── <project-build-id>/Build/Products/
        │       ├── Release-iphoneos/Runner.app
        │       └── Debug-iphoneos/Runner.app
        ├── swift-cache/                Swift and compiler caches
        ├── swift-shared/               Swift Package Manager state
        ├── sweetpad-state/              SweetPad build state
        ├── pub-active-roots/           Pub's writable workspace bookkeeping
        └── signing/                    Private signing inputs and USB output
            ├── iphone-udid.txt         Paired device ID
            ├── development.key         Private development signing key
            ├── development.cer         Apple Development certificate
            ├── AppleWWDRCAG3.cer        Example matching WWDR certificate
            ├── *.mobileprovision       App and extension provisioning profiles
            ├── *.entitlements          Generated signing entitlements
            ├── staged/Runner.app        Bundle with embedded profiles
            └── signed/Runner.app        Signed bundle ready for USB install
```

Some existing registrations keep their project state directly under
`~/sweetpad-ios/`. In that case, read the profile's `state` field before
looking for `source/app`, `derived-data`, or `signing`. Native dependency
overrides and vendor caches also belong to the project that needs them.

Keep build logs in the private project state and signing or USB install logs
in its private signing directory. Store Runner, LLDB, and Flutter attach logs
in a private location, since they can contain the authenticated Dart VM URL.
The tools accept explicit log paths, so those files can have different names.

For a standard Flutter project with a Runner workspace and Swift Package Manager, run these commands from a shell with `tools` and `flutter_sdk` defined. Point `app` at the directory that contains `pubspec.yaml`.

```bash
app="/path/to/flutter-app"
(cd "$app" && "$flutter_sdk/bin/flutter" pub get)

python3 "$tools/shared-ios/sweetpad-ios.py" register my-app "$app"
python3 "$tools/shared-ios/sweetpad-ios.py" check my-app
python3 "$tools/shared-ios/sweetpad-ios.py" prepare my-app
python3 "$tools/shared-ios/sweetpad-ios.py" build my-app
```

The first `flutter pub get` warms the package cache and creates or updates the lockfile in the app's own checkout. The shared runner uses the lockfile and prepares iOS dependencies offline. It keeps Pub's writable bookkeeping and generated Flutter/iOS files outside the checkout. The build prints the path to the unsigned `Runner.app`.

If `flutter pub get --enforce-lockfile` reports that it cannot satisfy the checked-in lockfile with the shared Flutter SDK, resolve the dependencies in a private copy of the app's package inputs. Start from the checked-in `pubspec.yaml` and `pubspec.lock`, include any local package inputs the app needs, and run `flutter pub get --no-example` with the same Flutter SDK used by SweetPad. Review the resulting lockfile changes. The shared runner's `local_lockfile` profile field points to this resolved lockfile relative to the project's private `state` directory. The runner uses it for offline preparation and compilation. Keep the repository lockfile intact and resolve the private copy again when the app's dependencies or shared SDK change. This was needed for Cruvio with Flutter 3.50.0.

Run `prepare` after dependency changes. For a custom iOS workspace, scheme, dependency layout, or generated localization directory, update the profile that `register` created. See the [example project profile](examples/project-profile.json). Mount generated files separately when Flutter needs to write them. Add public client settings through the profile's `public_defines_file` if the app needs them. Values in that file become part of the application. Keep Apple credentials, private keys, and backend service-role keys in separate private files.

## Stock app walkthrough

After setting up the tools in sections 1–3, generate Flutter's default iOS counter app and give it a bundle ID under a name your Apple Developer team can register. Keep the app outside the tools checkout.

```bash
app="$HOME/stock-flutter"
project=stock-flutter
org=com.example.sweetpad  # Replace with an identifier prefix for your team
runner="$tools/shared-ios/sweetpad-ios.py"
"$flutter_sdk/bin/flutter" create --platforms=ios --org "$org" \
  --project-name stock_flutter "$app"
grep 'PRODUCT_BUNDLE_IDENTIFIER =' "$app/ios/Runner.xcodeproj/project.pbxproj"
python3 "$runner" register "$project" "$app"
python3 "$runner" check "$project"
python3 "$runner" prepare "$project"
python3 "$runner" build "$project"
```

Use the Runner identifier without the `.RunnerTests` suffix. With the example prefix it is `com.example.sweetpad.stockFlutter`. The build prints the unsigned `Runner.app` path. The stock app needs no remote Swift packages, so proceed directly to signing.

1. Pair the phone and enable Developer Mode as in section 5. Register the Runner app ID in Apple Developer, then use section 6 to obtain an iOS App Development profile for that app ID, your phone, and your Apple Development certificate. [Register an App ID](https://developer.apple.com/help/account/identifiers/register-an-app-id/)
2. Install `rcodesign` as in section 7. In `$tools/projects/$project/signing/`, name the private key `development.key`, the leaf certificate `development.cer`, the matching WWDR intermediate `AppleWWDR.cer`, and the profile `Runner.mobileprovision`. Save the paired phone's UDID in `iphone-udid.txt` in the same directory.
3. Set `unsigned_app` to the path printed by the build. Connect and unlock the paired phone, then run the stock installer.

   ```bash
   python3 "$tools/shared-ios/install-stock-app.py" "$project" "$unsigned_app"
   ```

The helper checks the profile against the bundle, certificate, and phone. It signs a staged copy, installs it over USB, reads back the installed version, and launches it. Look for **Flutter Demo Home Page** and tap **+** to confirm the count increases. For a second build of the same app, set `SWEETPAD_BUILD_NUMBER` to a value above the installed build number when running `build`.

The stock app was verified from a fresh Flutter checkout, Pub cache, and project workspace on an iPhone. The test reused the matching imported Apple SDK and Linux iOS AOT compiler from sections 2 and 3.

## 5. Pair the iPhone and enable Developer Mode

Install the pinned USB adapter in a private Python environment. Connect the unlocked iPhone with a data cable and accept **Trust This Computer** when iOS asks.

```bash
python3 -m venv "$tools/device-tools"
"$tools/device-tools/bin/python" -m pip install pymobiledevice3==11.50.0 pexpect==4.9.0
export SWEETPAD_DEVICE_PYTHON="$tools/device-tools/bin/python"

"$SWEETPAD_DEVICE_PYTHON" -m pymobiledevice3 lockdown pair
"$tools/tools/sweetpad" device list --non-interactive
```

On the iPhone, open **Settings → Privacy & Security → Developer Mode**, enable it, restart, then unlock and confirm **Enable** with the passcode. If the switch is missing, ask the phone to reveal it and reopen Settings. An initial paired development app installation can also make the setting visible.

```bash
"$SWEETPAD_DEVICE_PYTHON" -m pymobiledevice3 amfi reveal-developer-mode
"$SWEETPAD_DEVICE_PYTHON" -m pymobiledevice3 amfi developer-mode-status
```

The status should be `true`. The first use of remote developer tools, such as programmatic launch or screen capture, can require a personalized developer disk image. `pymobiledevice3 mounter auto-mount` can fetch and personalize it through Apple's service. This step prepares the device tooling. It is separate from compiling, signing, and copying the app over USB. The downloaded SweetPad CLI also includes a `tools/LINUX_DEVICES.md` guide. [Apple Developer Mode instructions](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device/)

## 6. Get development signing assets for the device

Use the Apple Developer team that owns the app ID. Register the iPhone's UDID with that team. Get a development certificate with its matching private key and an **iOS App Development** provisioning profile for the main app and each extension. Each profile must select the certificate, include the test iPhone, and authorize the target's capabilities. The same team can use one development identity and device registration across multiple projects.

You can use an existing valid development identity. To make a new one, generate the key and certificate request locally. Keep the key private.

```bash
signing="$tools/projects/my-app/signing"
umask 077
test ! -e "$signing/development.key" && test ! -e "$signing/development.csr" || exit 1
openssl req -new -newkey rsa:2048 -noenc \
  -keyout "$signing/development.key" \
  -out "$signing/development.csr" \
  -subj '/CN=Local iOS Development'
openssl req -verify -noout -in "$signing/development.csr"
```

Submit only the CSR to Apple to obtain an Apple Development certificate. Download the certificate and development profiles into the private signing folder. Get the Apple WWDR intermediate certificate that matches the issuer on the development certificate. Our certificate used WWDR G3. Existing App Store Connect API credentials can also issue development certificates and profiles when they have the required permissions. We used that API for provisioning while keeping the private signing key and app bundle on Linux. [Register a device](https://developer.apple.com/help/account/devices/register-a-single-device/) · [Create a development provisioning profile](https://developer.apple.com/help/account/provisioning-profiles/create-a-development-provisioning-profile/) · [Apple WWDR intermediates](https://developer.apple.com/help/account/certificates/wwdr-intermediate-certificates/)

Use **iOS App Development** profiles for this local USB workflow. For an app with a widget or notification service, obtain a profile for each extension's bundle ID as well.

## 7. Sign and install the Release build

Download Linux `rcodesign` from the upstream Apple Codesign release, verify the published checksum, and place the binary at `$tools/signing-tools/rcodesign`. We used 0.29.0. The shared signing script invokes it with `--timestamp-url none` inside a network-disabled container. [Apple Codesign releases](https://github.com/indygreg/apple-platform-rs/releases) · [rcodesign signing options](https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_rcodesign_signing.html)

Stage the built app in the project's signing directory. Copy the matching profile into the main bundle and each extension bundle as `embedded.mobileprovision`. Generate an entitlement plist for each target using the profile and the target's entitlement settings. Preserve the application identifier, team identifier, keychain groups, and app capabilities that apply to that target. Before embedding a profile, check that it includes the selected phone, lists the signing certificate, and authorizes the required entitlements.

For a first app without additional capabilities, the following prepares a bundle copy with just the standard development entitlements. Set `unsigned_app` to the bundle path printed by the build and name the downloaded profile `Runner.mobileprovision`. Use a new staging directory.

```bash
mkdir -p "$signing/staged"
test ! -e "$signing/staged/Runner.app" || exit 1
cp -a "$unsigned_app" "$signing/staged/Runner.app"
cp "$signing/Runner.mobileprovision" "$signing/staged/Runner.app/embedded.mobileprovision"
openssl cms -verify -binary -inform DER -in "$signing/Runner.mobileprovision" \
  -noverify -out "$signing/profile.plist"
python3 -c '
import fnmatch, pathlib, plistlib, sys
root = pathlib.Path(sys.argv[1])
profile = plistlib.loads((root / "profile.plist").read_bytes())
info = plistlib.loads((root / "staged/Runner.app/Info.plist").read_bytes())
allowed = profile["Entitlements"]
app_id = allowed["application-identifier"]
assert app_id.endswith("." + info["CFBundleIdentifier"])
assert allowed["get-task-allow"] is True and profile["ProvisionedDevices"]
assert any(fnmatch.fnmatchcase(app_id, p) for p in allowed["keychain-access-groups"])
entitlements = {k: allowed[k] for k in (
    "application-identifier", "com.apple.developer.team-identifier", "get-task-allow",
)}
entitlements["keychain-access-groups"] = [app_id]
(root / "Runner.entitlements").write_bytes(plistlib.dumps(entitlements))
' "$signing"
```

For a single-target app, an example signing command is below. The input bundle already contains its provisioning profile. Use the actual paths to the staged app, certificate, and generated entitlement file.

```bash
mkdir -p "$signing/signed"
bash "$tools/shared-ios/sign-app.sh" "$signing" "$signing/signed" \
  --pem-file "$signing/development.key" \
  --certificate-der-file "$signing/development.cer" \
  --certificate-der-file "$signing/AppleWWDRCAG3.cer" \
  --entitlements-xml-file "$signing/Runner.entitlements" \
  "$signing/staged/Runner.app" "$signing/signed/Runner.app"

"$tools/signing-tools/rcodesign" verify "$signing/signed/Runner.app/Runner"
python3 "$tools/shared-ios/sweetpad-ios.py" install my-app "$signing/signed/Runner.app"
```

For an extension, add a scoped entitlement argument such as `--entitlements-xml-file "PlugIns/Example.appex:$signing/Example.entitlements"`. Verify the signature on the extension executable as well. For multiple connected devices, put the desired UDID in the project's private `signing/iphone-udid.txt`.

Before replacing an installed build, read its marketing version and build number through the USB installation service. Keep the same bundle identifier and compatible signing identity, and choose a build number above both the installed and previous local builds. After installation, read back the identifier and build number. Preserve the existing app and its data if the version or signing checks fail.

### Launch and verify over USB

A Release build can open from the Home Screen or from Linux. Set `bundle_id` to the installed app's identifier and save the paired device ID in the project's private signing directory. Read it into the environment without displaying it.

```bash
device_python="$tools/device-tools/bin/python"
export PYMOBILEDEVICE3_UDID="$(<"$signing/iphone-udid.txt")"
"$device_python" -m pymobiledevice3 developer dvt launch "$bundle_id"
"$device_python" -m pymobiledevice3 developer dvt process-id-for-bundle-id "$bundle_id"
```

A positive process ID confirms that iOS launched the app. Check the screen as well when verifying startup or a UI change. For app logs, use `pymobiledevice3 syslog live -pn Runner -o <private-log-file>`, substituting the app's executable name if it differs. Keep build, signing, and USB installation logs in the private workspace. If the developer service is unavailable, check pairing and Developer Mode, then use the developer image mounting step in section 5.

Take a screenshot with `pymobiledevice3 developer dvt screenshot <private-screenshot-path>`. For touch input, use `pymobiledevice3 developer core-device universal-hid-service tap <x> <y>`. Convert a screenshot point to touch coordinates with `round(x * 65535 / screenshot_width)` and `round(y * 65535 / screenshot_height)`. The command also supports `drag` and `type`. For a sequence of taps, use `session --script <script-path>` and capture screenshots from a second process. A session file can contain lines such as `tap 32767 50000` and `sleep 2`. Wait for the target screen to render before sending input, then check the visible result.

The USB check verifies local development signing and launch. Use the project's distribution workflow to verify App Store signing, distribution profiles, IPA export, or TestFlight.

## 8. Native dependency fixes from the first setup

| What stopped the build or install | What made it work |
| --- | --- |
| CocoaPods needed dependencies and tool versions available inside Docker | Installed CocoaPods 1.16.2, prepared its local cache and specs, and cleared the image's `SWIFT_VERSION` environment value for CocoaPods |
| The Firebase Swift package hid Apple products on a Linux host | Added a local Firebase manifest override for Core, Installations, and Messaging, then pointed the Flutter Firebase plugin manifests at the override |
| Mapbox's source-built Turf picked up an incompatible iOS version | Used the checksum-verified Turf 4.0.0 vendor XCFramework matching the lockfile and kept the app's iOS deployment target |
| Flutter's Objective-C native hook could not find a Linux compiler for iOS | Used the local SDK, an iOS-targeted clang shim, and the Mach-O linker |
| SQLite's native hook tried to fetch a dylib inside the offline build | Cached the exact iOS arm64 dylib expected by sqlite3 3.5.2 and checked its pinned SHA-256 |
| Linux GNU strip tried to process Mach-O binaries | Used LLVM's Mach-O tools and disabled Xcode's optional product stripping in this local Release build |
| iOS rejected a Sentry framework folder with no executable | Verified the Sentry code was linked statically and preserved its privacy manifest in a resource bundle before signing |
| Flutter tried to regenerate localization files under the read-only checkout | Mounted just the generated localization directory as writable private build state |

Keep native overrides in each project's private workspace. Recreating the same build can require patched manifests, staged CocoaPods files, Swift package caches or mirrors, and verified vendor artifacts. Preserve the local helper scripts, settings, signing identity, and profiles. The SDK, dependency downloads, compiler, and unsigned build outputs can be recreated.

The [Firebase 12.18.0 patch](examples/firebase-ios-sdk-12.18.0.patch) enables the Core, Installations, and Messaging products for this Linux-to-iOS build. Apply it with `git apply` in a private checkout of `firebase-ios-sdk` at tag `12.18.0`. Point the matching FlutterFire plugin manifests at that checkout with a Swift package path dependency, then point the generated Flutter package at the local plugin copies. Review the patch before reusing it for an app that needs other Firebase products.

For the SQLite workaround, the hook's cached file is `libsqlite3.dylib` under `source/app/.dart_tool/hooks_runner/shared/sqlite3/build/download-f1bc69a4/` in the private workspace. It comes from the sqlite3 3.5.2 release asset `libsqlite3.arm64.ios.dylib`. The package pins SHA-256 `f1bc69a4304a21e472c15f849c34ae46539483cfa7ce901f54175c6d6cc17991`. [SQLite release assets](https://github.com/simolus3/sqlite3.dart/releases/tag/sqlite3-3.5.2) · [Turf 4.0.0 release](https://github.com/mapbox/turf-swift/releases/tag/v4.0.0)

### Missing SDK headers during dependency scanning

Read the first errors in the build log. In the Cruvio build, `Too many open files` appeared before errors about missing UIKit and CoreImage headers that were present in the SDK. The container's soft limit was 1024. Raising it to the existing hard limit in the build entry point before starting SweetPad resolved the scanning failures.

```bash
ulimit -Sn "$(ulimit -Hn)"
```

### CocoaPods-only plugins and duplicate WebP symbols

If `GeneratedPluginRegistrant.m` reports `module '<plugin>' not found`, compare the generated registrant with the dependencies and products in `FlutterGeneratedPluginSwiftPackage/Package.swift`. Flutter registered Cruvio's `flutter_image_compress_common`, but the plugin shipped a podspec without a Swift package. We added a private Swift package for the same plugin sources with the podspec's native dependencies, then added its package and product to the generated Flutter package. Check the exact capitalization of native header names on Linux. Reapply the local package entry when Flutter regenerates the manifest during dependency preparation.

After adding image compression, the linker reported duplicate `WebP…` symbols from `libwebp.o` and PostHog's `phlibwebp.o`. The local build used one compatible libwebp for both packages by pointing PostHog's private Swift package and its WebP import at the same library used by the image compression package. Prepare the native sources and caches separately, then retry the offline build.

The [native package example](examples/native-packages/) contains the Swift manifests and a preparation script for `flutter_image_compress_common` 1.0.6, `posthog_flutter` 5.24.2, Mantle 2.2.0, and PostHog iOS 3.56.0. It copies the plugin sources from Pub and the matching upstream checkouts into your private project state. The two packages share libwebp 1.6.0.

Run this after warming Pub and running the project's `prepare` command. Use the same `PUB_CACHE` as the build. If Firebase is also in the app, apply the Firebase manifest and plugin overrides described above before resolving Swift packages.

```bash
state="$tools/projects/$project"
mkdir -p "$state/upstream" "$state/git-mirrors"
git clone --branch 2.2.0 --depth 1 https://github.com/Mantle/Mantle.git "$state/upstream/Mantle"
git clone --branch 3.56.0 --depth 1 https://github.com/PostHog/posthog-ios.git "$state/upstream/posthog-ios"
git clone --mirror https://github.com/webmproject/libwebp.git "$state/git-mirrors/libwebp.git"

python3 "$tools/examples/native-packages/prepare.py" \
  "$state" "$state/upstream/Mantle" "$state/upstream/posthog-ios"
python3 "$tools/shared-ios/sweetpad-ios.py" resolve-swift "$project"
python3 "$tools/shared-ios/sweetpad-ios.py" build "$project"
```

`resolve-swift` runs Swift Package Manager with network access and stores fetched Git repositories and binary artifacts in that project's `swift-cache`. The container mounts the generated package, dependency caches, and private Swift source copies needed for resolution. Build and signing still run offline. The libwebp mirror supplies the Git submodule used by the offline Swift build. After running `prepare` again, rerun the native package helper to restore the generated manifest entries.

### Sentry static framework copy failures

Check the full SweetPad task log if the build reports a Clang failure followed by an `llvm-lipo` error while copying `Sentry.framework`. In Cruvio, the copy task tried to remove the static executable and called the Linux linker. Passing `REMOVE_STATIC_EXECUTABLES_FROM_EMBEDDED_BUNDLES=NO` as a local build setting let the copy complete.

Before signing, check that the copied Sentry binary is a static archive, or that the framework contains only resources if Swift Build already removed the archive. Use `otool -L` or LLVM's `llvm-otool -L` to confirm Runner has no dynamic dependency on `Sentry.framework/Sentry`. Move `PrivacyInfo.xcprivacy` into a resource bundle in the staged app and take the static framework wrapper out of `Frameworks/`. Keep the unsigned build output for comparison. The Cruvio Release build passed USB installation and opened on the iPhone after this packaging step.

## 9. Build Debug and hot reload on a physical iPhone

This manual workflow was verified from Linux with LLDB 21 on a phone running iOS 27.2.1. The successful Debug build, signing, and USB install took about 2 minutes 14 seconds. The initial debugger connection and Flutter sync took additional time. Subsequent Dart reloads took from under a second to about four seconds in the trial.

The stock Flutter app also passed the shared-tunnel workflow below. A temporary title appeared after hot reload, and a second reload restored the original title while the iPhone process stayed the same.

### Build and install the Debug app

1. Use the same SweetPad Docker setup and native dependency caches as Release. Set a build number above the installed one. Update the project's `public_defines_file` if the Debug app needs different client settings. The runner writes these settings into the `Generated.xcconfig` included by the app's Debug configuration.

   ```bash
   SWEETPAD_BUILD_NUMBER=2 python3 "$tools/shared-ios/sweetpad-ios.py" build "$project" --configuration Debug
   ```

2. Use the path printed under `Debug-iphoneos`. For the stock app, run `install-stock-app.py` with `--install-only` so the app waits for a suspended launch. For an app with extra targets or entitlements, follow section 7 and preserve the Debug entitlements, including `get-task-allow`. Confirm `Frameworks/App.framework/flutter_assets/kernel_blob.bin` is present in the signed app before attaching.

### Launch under LLDB

Install LLDB 21 with Python bindings. On Ubuntu builds that package this version, the packages are `lldb-21` and `python3-lldb-21`. Set `signed_app` to the signed Debug bundle path. Use the same `SWEETPAD_FLUTTER_SDK` used to build the app and start the USB syslog capture in one terminal. Set `device_python`, `signing`, and `debug_logs` in both terminals and keep the logs private.

```bash
umask 077
device_python="$tools/device-tools/bin/python"
signing="$tools/projects/$project/signing"
debug_logs="$(mktemp -d "$signing/hot-reload.XXXXXX")"
export PYMOBILEDEVICE3_UDID="$(<"$signing/iphone-udid.txt")"
"$device_python" -m pymobiledevice3 syslog live -pn Runner \
  -m 'Dart VM service is listening on' -o "$debug_logs/vm-service.log" >/dev/null
```

In the second terminal, use the same `debug_logs` path and paired device. Launch the installed app suspended, then pass the printed process ID to the helper. Set `bundle_id` to the Runner identifier.

```bash
"$device_python" -m pymobiledevice3 developer dvt launch "$bundle_id" --suspended
pid=1234  # Replace with the process ID printed above
"$device_python" "$tools/shared-ios/debug-iphone.py" "$signed_app" \
  --pid "$pid" --lldb lldb-21 --logs "$debug_logs"
```

The helper uses the same USB userspace tunnel for the device service and the debugserver connection, as in the working XTRIM workflow. It loads the local Runner and Flutter binaries, prepares the JIT breakpoint before connecting, and resumes only stops from Flutter's JIT breakpoint. Check `lldb.log` if the helper stops. Leave the helper running while attaching Flutter.

### Attach Flutter and verify a reload

Watch the app's private log for **The Dart VM service is listening on**. Keep the authenticated URL out of shared output. Forward the phone's VM service port to a second Linux loopback port with `pymobiledevice3 usbmux forward <local-port> <phone-port> --serial "$PYMOBILEDEVICE3_UDID"`. In the logged URL, replace the host and port with that local endpoint and keep the authentication path intact. Allow the app's Local Network prompt if it appears during Debug launch.

From the Flutter app directory, attach with the same Dart defines that were used to build the app. The Linux device entry allows the local Flutter tool to start the attach session. The explicit VM URL connects it to the iPhone.

```bash
"$flutter_sdk/bin/flutter" attach -d linux \
  --debug-url "$flutter_vm_url" \
  --dart-define-from-file "$debug_defines_file" \
  --pid-file "$flutter_attach_pid_file"
```

Pass any extra build-time defines as additional `--dart-define` arguments. Keep attach output private because Flutter can print the authenticated URL. After the first sync, edit a visible Dart label and press `r` in the attach session. For automation, send `SIGUSR1` to the Flutter PID in the attach PID file. Confirm the label changed on the phone and that the iPhone process ID stayed the same. Restore the test label and reload again. Changes to native iOS code require a fresh build.

## Quick failure checks

| Symptom | Check |
| --- | --- |
| iPhone stays missing in SweetPad | Try the USB cable and host trust prompt, check `usbmuxd`, then use `sweetpad device list` |
| Developer Mode stays off after restarting | Unlock and finish the post-restart confirmation on the iPhone, then recheck its status from Linux |
| Provisioning or signature fails during install | Check the bundle IDs, profile expiration, phone UDID, signed capabilities, and matching key and certificate for every target |
| The installed app shows Flutter's debugger-required screen | For hot reload, launch Debug suspended and attach LLDB as in section 9. For ordinary Home Screen launch, use Release |
| The Debug app stays on its splash screen under LLDB | Use the local signed binaries and minimal device symbol loading, then check that the Flutter JIT breakpoint resolves and resumes |
| LLDB closes or stalls during the initial handshake | Use the shared-tunnel helper in section 9. It keeps the device services together and sets up the JIT breakpoint before connecting LLDB |
| Flutter attach cannot reach the phone | Check that the app published a Dart VM service URL, USB port forwarding is active, and the loopback URL retains its authentication path |
| The app shows cached data | Verify the build includes the public backend settings and check that the backend responded to the app's requests |
| SDK headers appear missing after `Too many open files` | Raise the soft file-descriptor limit inside the container before starting SweetPad, then retry |
| `flutter pub get --enforce-lockfile` rejects the checkout's lockfile | Resolve a private copy with the shared Flutter SDK and select it with `local_lockfile` |
| An offline `prepare` tries to fetch a package such as `test` | Initialize the Flutter SDK and fetch the app's packages with the same `PUB_CACHE` before copying the Flutter cache for Docker |
| `GeneratedPluginRegistrant.m` reports a missing plugin module | Check whether the plugin only ships a podspec and whether its package and product are in the generated Swift manifest |
| The linker reports duplicate `WebP…` symbols | Check which plugins supply libwebp and link one compatible copy |
| Clang or `llvm-lipo` fails while copying `Sentry.framework` | Check for the static archive removal step, then follow the local build setting and pre-signing packaging checks in section 8 |
| The offline build reports failed host lookups | Fetch the exact locked public dependency in a separate preparation step, then retry the offline build |
| The Flutter engine and AOT compiler differ | Build or obtain the Linux iOS compiler for the exact engine revision and update the private Flutter framework cache |

Keep build logs, SDK files, and signing files in the private workspace.
