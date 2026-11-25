"""Attach LLDB to a suspended Flutter Debug app over one USB tunnel."""

import argparse
import asyncio
import json
import os
import plistlib
import re
import threading
from pathlib import Path

import pexpect
from pymobiledevice3.cli.developer.debugserver import _forward_debugproxy
from pymobiledevice3.remote.userspace_tunnel import establish_userspace_rsd
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.process_control import ProcessControl
from pymobiledevice3.services.installation_proxy import InstallationProxyService


def run_lldb(lldb, commands, log_path, stop):
    with log_path.open("w", buffering=1) as log:
        with pexpect.spawn(lldb, ["--no-lldbinit", "--no-use-colors"], encoding="utf-8",
                           codec_errors="replace", timeout=90) as child:
            child.logfile_read = log
            child.expect_exact("(lldb)")
            breakpoint = None
            for command in commands:
                child.sendline(command)
                child.expect_exact("(lldb)")
                if re.search(r"(?m)^error:", child.before):
                    raise RuntimeError(f"LLDB could not run {command.split()[0]}. See {log_path}.")
                prepared = re.search(r"Flutter JIT breakpoint (\d+) prepared", child.before)
                if prepared:
                    breakpoint = prepared[1]
            if breakpoint is None:
                raise RuntimeError(f"LLDB did not prepare the Flutter breakpoint. See {log_path}.")

            jit_stop = re.compile(rf"breakpoint {breakpoint}\.\d+")
            child.sendline("process continue")
            while not stop.is_set():
                status = child.expect_exact(["(lldb)", pexpect.EOF, pexpect.TIMEOUT], timeout=1)
                if status == 2:
                    continue
                reasons = re.findall(r"stop reason = ([^\r\n]+)", child.before)
                if (status == 1 or "Flutter JIT callback failed." in child.before
                        or not reasons or any(not jit_stop.fullmatch(reason) for reason in reasons)):
                    raise RuntimeError(f"LLDB stopped or exited. See {log_path}.")
                child.sendline("process continue")


async def debug(args):
    app = args.app.resolve()
    info = plistlib.loads((app / "Info.plist").read_bytes())
    bundle = info["CFBundleIdentifier"]
    flutter = app / "Frameworks/Flutter.framework/Flutter"
    if not (app / "Frameworks/App.framework/flutter_assets/kernel_blob.bin").is_file():
        raise SystemExit("Pass the signed Flutter Debug app.")

    args.logs.mkdir(parents=True, exist_ok=True)
    device_log = args.logs / "device.log"
    debugger_log = args.logs / "lldb.log"
    helper = Path(__file__).with_name("flutter_jit_lldb.py").resolve()
    service = await establish_userspace_rsd(serial=os.environ["PYMOBILEDEVICE3_UDID"])
    async with (service, InstallationProxyService(lockdown=service) as installer,
                DvtProvider(service) as dvt, ProcessControl(dvt) as control):
        apps = await installer.get_apps(bundle_identifiers=[bundle])
        installed = apps.get(bundle)
        if installed is None or installed["CFBundleVersion"] != info["CFBundleVersion"]:
            raise SystemExit("Install this signed Debug build on the iPhone first.")
        pid = await control.process_identifier_for_bundle_identifier(bundle)
        if pid <= 0 or pid != args.pid:
            raise SystemExit("The suspended process ID does not belong to this app.")
        print(f"Attaching to {bundle} (PID {pid}). LLDB log: {debugger_log}", flush=True)
        print(f"Private device log: {device_log}", flush=True)

        async def read_device_log():
            with device_log.open("w", buffering=1) as log:
                async for event in control:
                    if event.pid == pid:
                        print(event.message, file=log)

        async with _forward_debugproxy(service) as port:
            commands = [
                "platform select remote-ios",
                "settings set target.memory-module-load-level minimal",
                "settings set target.preload-symbols false",
                "settings set symbols.enable-external-lookup false",
                f"target create {json.dumps(str(app / info['CFBundleExecutable']))}",
                f"target modules search-paths add {json.dumps(installed['Path'])} {json.dumps(str(app))}",
                f"target modules add {json.dumps(str(flutter))}",
                f"command script import {json.dumps(str(helper))}",
                "sweetpad-flutter-jit-prepare",
                f"process connect connect://127.0.0.1:{port}",
                f"process attach --pid {pid}",
                "script lldb.debugger.SetAsync(False)",
            ]
            stop = threading.Event()
            reader = asyncio.create_task(read_device_log())
            try:
                await asyncio.to_thread(run_lldb, args.lldb, commands, debugger_log, stop)
            finally:
                stop.set()
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--lldb", default="lldb-21")
    parser.add_argument("--pid", type=int, required=True, help="The suspended app process ID.")
    parser.add_argument("--logs", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if not os.environ.get("PYMOBILEDEVICE3_UDID"):
        parser.error("set PYMOBILEDEVICE3_UDID to the paired iPhone's UDID")
    try:
        asyncio.run(debug(args))
    except KeyboardInterrupt:
        return
    except pexpect.ExceptionPexpect as error:
        raise SystemExit(f"LLDB {type(error).__name__}. See {args.logs / 'lldb.log'}.") from None
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
