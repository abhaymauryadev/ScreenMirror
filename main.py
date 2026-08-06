"""
PyQt6 GUI for the Android screen mirror.
Displays the live frame stream and forwards mouse/keyboard input back to
the device via InputController.

Usage:
    python main.py                     # first device found
    python main.py --serial R58N...    # specific device (see `adb devices`)
    python main.py --max-size 0        # native resolution (slower)
"""
import sys
import queue
import argparse

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QImage, QPixmap, QMouseEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget

from streamer import ScreenStreamer, DeviceInfo
from input_controller import InputController

KEY_MAP = {
    Qt.Key.Key_Escape: "back",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
}


class MirrorWindow(QMainWindow):
    def __init__(self, serial=None, max_size=1280, bitrate="8M"):
        super().__init__()
        self.setWindowTitle("Android Mirror" + (f" ({serial})" if serial else ""))

        self.label = QLabel("Connecting...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.setCentralWidget(container)

        self.input = InputController(serial)
        self.streamer = ScreenStreamer(serial=serial, bitrate=bitrate, max_size=max_size)
        self.streamer.start()

        self.label.setMouseTracking(True)
        self._drag_start = None

        self.timer = QTimer()
        self.timer.timeout.connect(self._update_frame)
        self.timer.start(16)  # poll at ~60Hz; actual rate is capped by the stream

        self.resize(min(self.streamer.width, 480), min(self.streamer.height, 900))

    # ---- frame display ---------------------------------------------
    def _update_frame(self):
        try:
            frame = self.streamer.frame_queue.get_nowait()
        except queue.Empty:
            return
        h, w, _ = frame.shape
        img = QImage(frame.data, w, h, 3 * w, QImage.Format.Format_BGR888)
        pix = QPixmap.fromImage(img).scaled(
            self.label.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.label.setPixmap(pix)

    # ---- coordinate mapping (GUI pixels -> device pixels) -----------
    def _map_to_device(self, pos: QPoint):
        pix = self.label.pixmap()
        if pix is None or pix.isNull():
            return None
        label_w, label_h = self.label.width(), self.label.height()
        pix_w, pix_h = pix.width(), pix.height()
        off_x = (label_w - pix_w) / 2  # letterboxing offset
        off_y = (label_h - pix_h) / 2
        x = pos.x() - off_x
        y = pos.y() - off_y
        if not (0 <= x <= pix_w and 0 <= y <= pix_h):
            return None
        dx = x / pix_w * self.streamer.width
        dy = y / pix_h * self.streamer.height
        return dx, dy

    # ---- mouse events -------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        pos = self._map_to_device(event.position().toPoint())
        if pos:
            self._drag_start = pos

    def mouseReleaseEvent(self, event: QMouseEvent):
        pos = self._map_to_device(event.position().toPoint())
        if pos and self._drag_start:
            x1, y1 = self._drag_start
            x2, y2 = pos
            if abs(x2 - x1) < 5 and abs(y2 - y1) < 5:
                self.input.tap(x2, y2)
            else:
                self.input.swipe(x1, y1, x2, y2)
        self._drag_start = None

    # ---- keyboard events ----------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        name = KEY_MAP.get(event.key())
        if name:
            self.input.key(name)
        elif event.text():
            self.input.text(event.text())

    def closeEvent(self, event):
        self.streamer.stop()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="Android screen mirror over ADB")
    parser.add_argument("--serial", default=None, help="Device serial (see `adb devices`)")
    parser.add_argument("--max-size", type=int, default=1280, help="Longest edge in px, 0 for native")
    parser.add_argument("--bitrate", default="8M")
    args = parser.parse_args()

    if args.serial is None:
        devices = DeviceInfo.list_devices()
        if not devices:
            print("No adb devices found. Connect a device with USB debugging enabled "
                  "(or `adb connect <ip>` for wireless) and try again.")
            sys.exit(1)
        if len(devices) > 1:
            print(f"Multiple devices found: {devices}. Pass --serial to pick one.")
            sys.exit(1)
        args.serial = devices[0]

    app = QApplication(sys.argv)
    win = MirrorWindow(serial=args.serial, max_size=args.max_size, bitrate=args.bitrate)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
