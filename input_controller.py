"""
Sends touch, swipe, and keyboard events back to the Android device via ADB.
Coordinates passed in must already be in device pixel space (the GUI layer
is responsible for scaling from window/label space to device space).

Note on latency: each call here spawns a fresh `adb shell` process, which
costs tens of milliseconds. That's fine for taps and key presses, but
makes continuous drags feel stepped rather than fluid. scrcpy avoids this
by pushing a small native input daemon (minitouch-style) to the device
and talking to it over a persistent socket. If dragging feels too laggy,
that's the next thing to build - this module keeps things adb-only for
simplicity.
"""
import subprocess
import shlex
import sys

KEYEVENT_MAP = {
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "recent": "KEYCODE_APP_SWITCH",
    "power": "KEYCODE_POWER",
    "volume_up": "KEYCODE_VOLUME_UP",
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "enter": "KEYCODE_ENTER",
    "backspace": "KEYCODE_DEL",
    "menu": "KEYCODE_MENU",
    "tab": "KEYCODE_TAB",
}


class InputController:
    def __init__(self, serial=None):
        self.serial = serial

    def _adb(self, *args):
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"adb command failed ({' '.join(cmd)}): {result.stderr.strip()}",
                  file=sys.stderr)

    def tap(self, x, y):
        self._adb("shell", "input", "tap", str(int(x)), str(int(y)))

    def swipe(self, x1, y1, x2, y2, duration_ms=150):
        self._adb("shell", "input", "swipe",
                   str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(duration_ms))

    def key(self, name):
        code = KEYEVENT_MAP.get(name)
        if code is None:
            raise ValueError(f"Unknown key name: {name}")
        self._adb("shell", "input", "keyevent", code)

    def text(self, s):
        # Android's `input text` treats literal spaces as argument
        # separators, so they need to be escaped as %s first.
        escaped = s.replace(" ", "%s")
        self._adb("shell", "input", "text", shlex.quote(escaped))
