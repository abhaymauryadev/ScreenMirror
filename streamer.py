"""
Handles screen capture from an Android device over ADB and decodes the
H.264 stream into raw BGR frames for display.

Pipeline:
    adb exec-out screenrecord --output-format=h264 -   (raw H.264 elementary stream)
        |
    ffmpeg -i pipe:0 -f rawvideo -pix_fmt bgr24 pipe:1  (decode to raw frames)
        |
    Python reads WIDTH*HEIGHT*3 byte chunks -> numpy arrays

Known limitation: Android's `screenrecord` refuses to run longer than
180 seconds per invocation. This module transparently restarts the
adb/ffmpeg pair when that happens so the stream keeps going, at the
cost of a short (~200-500ms) hiccup every 3 minutes. The "real" fix
(what scrcpy does) is to push a small on-device server APK that uses
MediaCodec directly and streams over a raw socket with no time limit -
out of scope for this prototype but worth knowing about if you outgrow
this approach.
"""
import subprocess
import threading
import queue
import re
import shutil
import time
import numpy as np


class DeviceInfo:
    def __init__(self, serial=None):
        self.serial = serial

    def _adb(self, *args):
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    def get_resolution(self):
        out = self._adb("shell", "wm", "size").stdout
        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            raise RuntimeError(f"Could not parse resolution from: {out!r}")
        return int(m.group(1)), int(m.group(2))

    @staticmethod
    def list_devices():
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        lines = [l for l in out.splitlines()[1:] if l.strip()]
        return [l.split()[0] for l in lines if "device" in l]


class ScreenStreamer:
    """
    Streams frames from the device in a background thread and exposes them
    through a bounded queue. The queue drops old frames rather than
    growing unbounded, which keeps end-to-end latency low at the cost of
    occasionally skipping a frame under load - the right tradeoff for a
    live mirror.
    """

    def __init__(self, serial=None, bitrate="8M", max_size=1280, queue_size=2):
        self.serial = serial
        self.bitrate = bitrate
        self.max_size = max_size  # 0 = device native resolution
        self.frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=queue_size)
        self._manager_thread = None
        self._stop_event = threading.Event()
        self.width = 0
        self.height = 0

        if shutil.which("adb") is None:
            raise RuntimeError("adb not found on PATH. Install Android platform-tools.")
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH. Install ffmpeg.")

    def start(self):
        info = DeviceInfo(self.serial)
        self.width, self.height = info.get_resolution()
        if self.max_size:
            scale = self.max_size / max(self.width, self.height)
            if scale < 1:
                self.width = int(self.width * scale) // 2 * 2
                self.height = int(self.height * scale) // 2 * 2

        self._stop_event.clear()
        self._manager_thread = threading.Thread(target=self._manager_loop, daemon=True)
        self._manager_thread.start()

    def _spawn_pipeline(self):
        adb_cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + [
            "exec-out", "screenrecord",
            "--output-format=h264",
            f"--bit-rate={self.bitrate}",
        ]
        if self.max_size:
            adb_cmd.append(f"--size={self.width}x{self.height}")
        adb_cmd.append("-")

        adb_proc = subprocess.Popen(adb_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        ffmpeg_cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=adb_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return adb_proc, ffmpeg_proc

    def _manager_loop(self):
        """Runs the capture pipeline, restarting it whenever it dies
        (e.g. hitting screenrecord's 180s limit) until stop() is called."""
        while not self._stop_event.is_set():
            adb_proc, ffmpeg_proc = self._spawn_pipeline()
            try:
                self._read_frames(ffmpeg_proc)
            finally:
                for p in (ffmpeg_proc, adb_proc):
                    if p.poll() is None:
                        p.terminate()
            if not self._stop_event.is_set():
                time.sleep(0.2)  # brief pause before restart

    def _read_frames(self, ffmpeg_proc):
        frame_size = self.width * self.height * 3
        buf = b""
        stdout = ffmpeg_proc.stdout
        while not self._stop_event.is_set():
            chunk = stdout.read(frame_size - len(buf))
            if not chunk:
                return  # pipeline ended (time limit, disconnect, etc.)
            buf += chunk
            if len(buf) < frame_size:
                continue
            frame = np.frombuffer(buf, dtype=np.uint8).reshape((self.height, self.width, 3))
            buf = b""
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self.frame_queue.put(frame)

    def stop(self):
        self._stop_event.set()
        if self._manager_thread:
            self._manager_thread.join(timeout=2)
