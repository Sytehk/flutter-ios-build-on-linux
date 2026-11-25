"""Development-sign and install a stock Flutter Runner from the local build."""

import argparse
import datetime
import fnmatch
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("app", type=Path)
    parser.add_argument("--install-only", action="store_true", help="Leave a Debug app ready for a suspended launch.")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.project):
        parser.error("use a lowercase project name with letters, digits, or dashes")

    os.umask(0o077)
    tools = Path(__file__).resolve().parents[1]
    signing = tools / "projects" / args.project / "signing"
    profile = signing / "Runner.mobileprovision"
    certificate = signing / "development.cer"
    udid = (signing / "iphone-udid.txt").read_text().strip()
    env = dict(os.environ, PYMOBILEDEVICE3_UDID=udid)

    def device(*command):
        return subprocess.check_output([
            tools / "device-tools/bin/python", "-m", "pymobiledevice3", *command,
        ], env=env, timeout=60)

    if any(args.app.glob("PlugIns/*.appex")):
        raise SystemExit("This stock-app helper expects a single target. Sign extensions separately.")
    info = plistlib.loads((args.app / "Info.plist").read_bytes())
    bundle = info["CFBundleIdentifier"]
    version = info["CFBundleShortVersionString"]
    build = info["CFBundleVersion"]
    executable = info["CFBundleExecutable"]
    decoded = plistlib.loads(subprocess.check_output([
        "openssl", "cms", "-verify", "-binary", "-inform", "DER", "-in", profile, "-noverify",
    ], stderr=subprocess.DEVNULL))
    allowed = decoded["Entitlements"]
    app_pattern = allowed["application-identifier"]
    app_id = app_pattern.split(".", 1)[0] + "." + bundle
    expires = decoded["ExpirationDate"].replace(tzinfo=datetime.timezone.utc)
    if (expires <= datetime.datetime.now(datetime.timezone.utc)
            or udid not in decoded.get("ProvisionedDevices", ())
            or certificate.read_bytes() not in decoded["DeveloperCertificates"]
            or allowed.get("get-task-allow") is not True
            or not fnmatch.fnmatchcase(app_id, app_pattern)
            or not any(fnmatch.fnmatchcase(app_id, group) for group in allowed["keychain-access-groups"])):
        raise SystemExit("The development profile must authorize this bundle, iPhone, and signing certificate.")

    current = json.loads(device("apps", "list", "--type", "User")).get(bundle)
    if current and (current.get("Entitlements", {}).get("application-identifier") != app_id
                    or int(current["CFBundleVersion"]) >= int(build)):
        raise SystemExit("The installed app requires a matching identity and a higher build number.")

    run = Path(tempfile.mkdtemp(prefix=f"build-{build}-", dir=signing))
    staged = run / "staged" / args.app.name
    output = run / "signed"
    output.mkdir()
    shutil.copytree(args.app, staged, symlinks=True)
    shutil.copyfile(profile, staged / "embedded.mobileprovision")
    entitlements = run / "Runner.entitlements"
    entitlements.write_bytes(plistlib.dumps({
        "application-identifier": app_id,
        "com.apple.developer.team-identifier": allowed["com.apple.developer.team-identifier"],
        "get-task-allow": True,
        "keychain-access-groups": [app_id],
    }))

    signed = output / args.app.name
    commands = (
        ["bash", tools / "shared-ios/sign-app.sh", signing, output,
         "--pem-file", signing / "development.key", "--certificate-der-file", certificate,
         "--certificate-der-file", signing / "AppleWWDR.cer", "--entitlements-xml-file", entitlements,
         staged, signed],
        [tools / "signing-tools/rcodesign", "verify", "-C", "/dev/null", signed / executable],
        ["python3", tools / "shared-ios/sweetpad-ios.py", "install", args.project, signed],
    )
    for step, command in zip(("sign", "verify", "install"), commands, strict=True):
        log_path = run / f"{step}.log"
        with log_path.open("wb") as log:
            if subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env).returncode:
                raise SystemExit(f"{step.capitalize()} failed. See {log_path}.")

    updated = json.loads(device("apps", "list", "--type", "User"))[bundle]
    if (updated["CFBundleShortVersionString"], updated["CFBundleVersion"]) != (version, build):
        raise SystemExit("The iPhone did not report the expected version and build.")
    print(f"Installed {bundle} {version} ({build}) over USB.", flush=True)
    print(f"Signed app: {signed}", flush=True)
    if not args.install_only:
        (run / "launch.log").write_bytes(device("developer", "dvt", "launch", bundle))
        pid = int(device("developer", "dvt", "process-id-for-bundle-id", bundle))
        if pid <= 0:
            raise SystemExit("The iPhone did not report a running app process.")
        print(f"Launched on iPhone (PID {pid}).")


if __name__ == "__main__":
    main()
