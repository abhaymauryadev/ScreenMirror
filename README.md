# android_mirror

A minimal Python screen-mirroring tool for Android over ADB: view your phone's
screen in a desktop window and send taps/swipes/keystrokes back to it.

This is a learning/prototype project, not a scrcpy replacement — see
**Limitations** below for exactly where it falls short and why.

## How it works

```
Android device
    │  adb exec-out screenrecord --output-format=h264 -
    ▼
raw H.264 elementary stream (stdout pipe)
    │  ffmpeg -i pipe:0 -f rawvideo -pix_fmt bgr24 pipe:1
    ▼
raw BGR24 frames (stdout pipe)
    │  numpy.frombuffer(...).reshape(h, w, 3)
    ▼
frame queue (bounded, drops stale frames)
    │  QTimer polls at ~60Hz
    ▼
QImage → QPixmap → QLabel  (main.py)

Mouse click/drag on QLabel
    │  scaled from window pixels → device pixels
    ▼
adb shell input tap / swipe   (input_controller.py)
```

## Prerequisites

- Python 3.9+
- `adb` on your PATH (Android platform-tools)
- `ffmpeg` on your PATH
- A device with USB debugging enabled, connected via USB or `adb connect <ip>:5555`
  for wireless (run `adb tcpip 5555` over USB first to enable it)

## Setup

```bash
pip install -r requirements.txt
adb devices          # confirm your device shows up as "device", not "unauthorized"
python main.py
```

Useful flags:

```bash
python main.py --serial R58N123ABCD   # pick a device when multiple are connected
python main.py --max-size 0           # native resolution (higher quality, more CPU/bandwidth)
python main.py --bitrate 4M           # lower bitrate for slow connections
```

## Controls

- Click = tap, click-drag = swipe
- Typed characters are forwarded as text input
- Esc = Android back button, Enter = enter, Backspace = delete

## Limitations (and what "real" scrcpy does instead)

1. **180-second capture limit.** Android's `screenrecord` command refuses to
   run longer than 3 minutes per invocation — it's a hard OS limit, not
   something you can configure around. This tool auto-restarts the
   adb/ffmpeg pipeline when that happens, which causes a brief (~0.2-0.5s)
   freeze every 3 minutes. scrcpy avoids this entirely by pushing a small
   on-device Java server that talks to `MediaCodec` directly and streams
   over a raw socket with no time limit.

2. **Input latency on drags.** Each tap/swipe here spawns a new `adb shell`
   process, adding tens of milliseconds of overhead. Taps feel fine;
   continuous dragging feels stepped rather than fluid. scrcpy solves this
   with a persistent socket to a `minitouch`-style native daemon on the
   device. Adding that here would mean cross-compiling and pushing a small
   native binary — a good next step if input feel matters to you.

3. **Decode path.** Frames go through a full ffmpeg subprocess rather than
   hardware-accelerated decode in-process, so at high resolution/bitrate
   you'll see meaningfully more CPU usage and latency than scrcpy's
   direct MediaCodec → SDL rendering pipeline.

4. **No audio.** This only mirrors video and forwards input; no audio
   forwarding is implemented.

None of these are fundamental blockers for a personal-use or learning tool —
they're exactly the reasons a production-grade tool like scrcpy ends up
written in C with a custom on-device server component instead of shelling
out to `screenrecord` and `adb input`.
