"""Build Flutter iPhone apps locally with a shared SweetPad installation."""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
PROJECTS = TOOLS / "projects"
SDK = Path(os.environ.get("SWEETPAD_FLUTTER_SDK", Path.home() / "development/flutter")).resolve()
PUB_CACHE = Path(os.environ.get("PUB_CACHE", Path.home() / ".pub-cache")).resolve()
IMAGE = "sweetpad-local:0.1.29"
IOS = ("Runner", "RunnerTests", "Runner.xcodeproj", "Runner.xcworkspace")


def project_file(name):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise SystemExit("Use a project name with lowercase letters, numbers, or dashes.")
    return PROJECTS / f"{name}.json"


def register(name, app):
    app = app.resolve()
    if app.is_relative_to(TOOLS):
        raise SystemExit("Use the project's own checkout outside the SweetPad tool folder.")
    if not (app / "pubspec.yaml").is_file() or not (app / "ios/Runner.xcworkspace").is_dir():
        raise SystemExit("Select the Flutter app folder that contains pubspec.yaml and ios/Runner.xcworkspace.")
    profile = project_file(name)
    state = PROJECTS / name
    if profile.exists() or state.exists():
        raise SystemExit(f"{name} already has a private local project folder.")
    inputs = ["lib", "pubspec.yaml"]
    inputs.extend(p for p in ("assets", "l10n.yaml", ".metadata") if (app / p).exists())
    inputs.extend(
        f"ios/{p}" for p in IOS if p != "Runner" and (app / "ios" / p).exists()
    )
    inputs.extend(
        p.relative_to(app).as_posix()
        for p in sorted((app / "ios/Runner").iterdir())
        if not p.name.startswith(("GeneratedPluginRegistrant.", "."))
        and p.name != "GoogleService-Info.plist"
    )
    inputs.extend(
        f"ios/Flutter/{p}" for p in ("AppFrameworkInfo.plist", "Debug.xcconfig", "Release.xcconfig")
        if (app / "ios/Flutter" / p).is_file()
    )
    generated = ["lib/l10n/generated"] if (app / "lib/l10n/generated").is_dir() else []
    (state / "source/app/ios/Flutter").mkdir(parents=True)
    state.joinpath("signing").mkdir()
    profile.write_text(json.dumps({
        "source": str(app),
        "state": state.relative_to(TOOLS).as_posix(),
        "inputs": inputs,
        "generated": generated,
    }, indent=2) + "\n")
    print(f"Registered {name}. Project files and signing assets stay under {state}.")


def load_project(name):
    path = project_file(name)
    if not path.is_file():
        raise SystemExit(f"Register {name} first.")
    profile = json.loads(path.read_text())
    source = Path(profile["source"]).resolve()
    state = (TOOLS / profile["state"]).resolve()
    if source == state or source.is_relative_to(state) or state.is_relative_to(source):
        raise SystemExit("The source and generated build workspace must be separate.")
    if source.is_relative_to(TOOLS):
        raise SystemExit("Use the project's own checkout outside the SweetPad tool folder.")
    for name in profile["inputs"] + profile.get("generated", []):
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise SystemExit(f"Use an app-relative input path: {name}")
        if not (source / name).exists():
            raise SystemExit(f"Missing input mount point in the app: {source / name}")
    engine = (SDK / "bin/internal/engine.version").read_text().strip()
    compiler = json.loads((TOOLS / "aot-compiler/build.json").read_text())
    if compiler["engine"] != engine:
        raise SystemExit("The shared iOS compiler and installed Flutter engine versions differ.")
    return source, state, profile


def prepare_settings(state, source, profile):
    generated = state / "source/app/ios/Flutter/Generated.xcconfig"
    if profile.get("prepare_manually") and generated.is_file():
        return
    worktree = Path(profile.get("container_state", state)) / "source/app"
    match = re.search(r"(?m)^version:\s*([0-9.]+)(?:\+(\d+))?", (source / "pubspec.yaml").read_text())
    version, number = match.groups() if match else ("1.0.0", "1")
    number = os.environ.get("SWEETPAD_BUILD_NUMBER", profile.get("build_number", number))
    settings = json.loads((source / profile["public_defines_file"]).read_text()) if profile.get("public_defines_file") else {}
    defines = ",".join(
        base64.b64encode(f"{name}={value}".encode()).decode()
        for name, value in settings.items()
    )
    generated.write_text(
        f"FLUTTER_ROOT={SDK}\nFLUTTER_APPLICATION_PATH={worktree}\nFLUTTER_TARGET=lib/main.dart\n"
        f"FLUTTER_BUILD_DIR=build\nFLUTTER_BUILD_NAME={version}\nFLUTTER_BUILD_NUMBER={number or '1'}\n"
        f"DART_DEFINES={defines}\n"
    )


def offline_container(source, state, profile, action, configuration):
    worktree = state / "source/app"
    container_state = Path(profile.get("container_state", state))
    container_worktree = container_state / "source/app"
    container_tools = container_state if state == TOOLS else TOOLS
    shared_config = TOOLS / "docker-config"
    (worktree / "ios/Flutter").mkdir(parents=True, exist_ok=True)
    contents = [
        (SDK, SDK, True),
        (PUB_CACHE, PUB_CACHE, True),
        (TOOLS / "flutter-cache", SDK / "bin/cache", False),
        (TOOLS / "aot-compiler/gen_snapshot", SDK / "bin/cache/artifacts/engine/ios-release/gen_snapshot_arm64", True),
    ]
    for name, target in (
        ("swift-cache", "/root/.cache"),
        ("swift-shared", "/root/.swiftpm"),
        ("derived-data", "/root/Library/Developer/Xcode/DerivedData"),
        ("sweetpad-state", "/root/.local/state/sweetpad"),
    ):
        directory = state / name
        directory.mkdir(exist_ok=True)
        contents.append((directory, target, False))
    if state == TOOLS:
        # Retain the paths embedded in XTRIM's existing Flutter and Swift caches.
        contents.append((TOOLS, container_tools, False))
    else:
        for name in ("apple-shims", "sweetpad-native-tools", "tools", "shared-ios", "darwin.artifactbundle", "prepare_ios_plugins.dart"):
            path = TOOLS / name
            contents.append((path, path, True))
        contents.append((state, container_state, False))
    for name in profile["inputs"]:
        contents.append((source / name, container_worktree / name, True))
    lockfile = state / profile["local_lockfile"] if "local_lockfile" in profile else source / "pubspec.lock"
    if lockfile.is_file():
        if action == "prepare":
            local_lock = worktree / "pubspec.lock"
            if not local_lock.is_file() or local_lock.read_bytes() != lockfile.read_bytes():
                new_lock = local_lock.with_suffix(".incoming")
                new_lock.write_bytes(lockfile.read_bytes())
                new_lock.replace(local_lock)
        else:
            contents.append((lockfile, container_worktree / "pubspec.lock", True))
    for name in profile.get("generated", []):
        local = worktree / name
        local.mkdir(parents=True, exist_ok=True)
        contents.append((local, container_worktree / name, False))
    for local_path, target in profile.get("overrides", {}).items():
        contents.append((state / local_path, target, True))
    if action == "prepare":
        active_roots = state / "pub-active-roots"
        active_roots.mkdir(exist_ok=True)
        contents.append((active_roots, PUB_CACHE / "active_roots", False))

    option = ["prepare"] if action == "prepare" else [
        profile.get("xcode_workspace", "ios/Runner.xcworkspace"),
        profile.get("scheme", "Runner"),
        configuration,
        *profile.get("build_settings", []),
    ]
    command = ["docker", "--config", shared_config, "run", "--rm", "--network", "none"]
    for mount, destination, read_only in contents:
        command.extend(("--mount", f"type=bind,source={mount},target={destination}" + (",readonly" if read_only else "")))
    for key, value in {
        "SWEETPAD_SHARED_TOOLS": container_tools,
        "SWEETPAD_PROJECT_STATE": container_state,
        "SWEETPAD_DARWIN_SDK": container_tools / "darwin.artifactbundle",
        "FLUTTER_ROOT": SDK,
        "FLUTTER_ALREADY_LOCKED": "true",
        "SWEETPAD_HOST_UID": os.getuid(),
        "SWEETPAD_HOST_GID": os.getgid(),
        "PUB_CACHE": PUB_CACHE,
        "CI": "true",
        "LANG": "C.UTF-8",
    }.items():
        command.extend(("-e", f"{key}={value}"))
    command.extend(("-w", container_worktree, "--entrypoint", "/bin/bash", IMAGE, container_tools / "shared-ios/container-build.sh", *option))
    return subprocess.run(command).returncode


def resolve_swift_packages(state):
    app = state / "source/app"
    packages = app / "ios/Flutter/ephemeral/Packages"
    generated_package = packages / "FlutterGeneratedPluginSwiftPackage"
    if not (generated_package / "Package.swift").is_file():
        raise SystemExit("Run the project's prepare step before resolving Swift packages.")
    cache = state / "swift-cache"
    cache.mkdir(exist_ok=True)
    command = ["docker", "--config", TOOLS / "docker-config", "run", "--rm", "--network", "bridge"]
    mounts = [(packages, packages, False), (cache, "/root/.cache", False),
              (SDK, SDK, True), (PUB_CACHE, PUB_CACHE, True)]
    for directory in (app / "build/ios/SourcePackages", state / "native-packages"):
        if directory.is_dir():
            mounts.append((directory, directory, True))
    for source, target, read_only in mounts:
        command.extend(("--mount", f"type=bind,source={source},target={target}" + (",readonly" if read_only else "")))
    command.extend(("-w", generated_package, "--entrypoint", "swift", IMAGE, "package", "resolve"))
    return subprocess.run(command).returncode


def install(state, app):
    if not app or not (app / "Info.plist").is_file():
        raise SystemExit("Provide the path to the development-signed .app to install via USB.")
    device = state / "signing/iphone-udid.txt"
    env = dict(os.environ, SWEETPAD_DEVICE_PYTHON=str(TOOLS / "device-tools/bin/python"))
    env.pop("SWEETPAD_DEVICE_NETWORK_CONFIG", None)
    command = [TOOLS / "tools/sweetpad", "device", "install", app.resolve(), "--non-interactive"]
    if device.is_file():
        command.extend(("--device", device.read_text().strip()))
    return subprocess.run(command, env=env).returncode


def dependency_fingerprint(source, state, profile):
    lock = source / "pubspec.lock"
    if not lock.is_file():
        raise SystemExit("Create or restore this Flutter project's pubspec.lock before preparing.")
    manifest = re.sub(rb"(?m)^version:.*$", b"", (source / "pubspec.yaml").read_bytes())
    inputs = manifest + b"\0" + lock.read_bytes()
    if "local_lockfile" in profile:
        inputs += b"\0" + (state / profile["local_lockfile"]).read_bytes()
    return hashlib.sha256(inputs).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "check", "prepare", "resolve-swift", "build", "install"))
    parser.add_argument("project")
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--configuration", choices=("Release", "Debug"), default="Release")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "register":
        if args.source is None:
            parser.error("register requires the Flutter app folder")
        register(args.project, args.source)
        return
    source, state, profile = load_project(args.project)
    if args.action == "install":
        raise SystemExit(install(state, args.source))
    if args.source is not None and source != args.source.resolve():
        raise SystemExit("The registered SweetPad project points to a different checkout.")
    print(f"{args.project}: {source} → {state}", flush=True)
    if args.action == "check":
        return
    if args.action == "prepare" and profile.get("prepare_manually"):
        raise SystemExit("This project's native dependency overrides require its existing manual preparation.")
    with (TOOLS / "shared-flutter.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "resolve-swift":
            raise SystemExit(resolve_swift_packages(state))
        worktree = state / "source/app"
        manual = profile.get("prepare_manually", False)
        stamp = state / "prepared-dependencies.sha256"
        if not manual:
            fingerprint = dependency_fingerprint(source, state, profile)
            if args.action == "build" and (not stamp.is_file() or stamp.read_text() != fingerprint):
                raise SystemExit("Run the project's offline prepare step after changing dependencies.")
        prepare_settings(state, source, profile)
        if args.action == "build" and not (worktree / ".dart_tool/package_config.json").is_file():
            raise SystemExit("Run the project's offline prepare step before building.")
        status = offline_container(source, state, profile, args.action, args.configuration)
        if status != 0:
            raise SystemExit(status)
        if args.action == "prepare":
            stamp.write_text(fingerprint)
        else:
            for app in sorted((state / "derived-data").glob(f"*/Build/Products/{args.configuration}-iphoneos/*.app")):
                print(f"Built unsigned iPhone app: {app}")


if __name__ == "__main__":
    main()
