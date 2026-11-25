"""Prepare Flutter's JIT breakpoint before connecting LLDB to the phone."""

import os
import re
import textwrap
from pathlib import Path

import lldb


def load_flutter_callback():
    sdk = Path(os.environ.get("SWEETPAD_FLUTTER_SDK", Path.home() / "development/flutter"))
    source = sdk / "packages/flutter_tools/lib/src/ios/lldb.dart"
    script = re.search(r"static const _pythonScript = '''(.*?)''';", source.read_text(), re.DOTALL)
    if script is None or "IHELPED!" not in script[1]:
        raise ValueError("The Flutter SDK's iOS debugger callback has changed.")
    namespace = {"lldb": lldb}
    body = textwrap.indent(textwrap.dedent(script[1]).strip(), "    ")
    exec("def callback(frame):\n" + body + "\n    return True", namespace)
    return namespace["callback"]


def touch_pages(frame, _location, _internal_dict):
    if flutter_callback(frame) is not True:
        print("Flutter JIT callback failed.", flush=True)
    return True


def prepare(debugger, _command, result, _internal_dict):
    global flutter_callback
    target = debugger.GetSelectedTarget()
    try:
        flutter_callback = load_flutter_callback()
    except (OSError, ValueError) as error:
        result.SetError(str(error))
        return

    breakpoint = target.BreakpointCreateByName("NOTIFY_DEBUGGER_ABOUT_RX_PAGES")
    if breakpoint.GetNumLocations() == 0:
        target.BreakpointDelete(breakpoint.GetID())
        result.SetError("The Flutter JIT breakpoint did not resolve. Add the local Flutter executable as a module.")
        return
    breakpoint.SetScriptCallbackFunction(f"{__name__}.touch_pages")
    result.AppendMessage(f"Flutter JIT breakpoint {breakpoint.GetID()} prepared.")


def __lldb_init_module(debugger, _internal_dict):
    debugger.HandleCommand(f"command script add -f {__name__}.prepare sweetpad-flutter-jit-prepare")
