"""
QR-code wireless pairing.

Flow:
    1. We start a tiny local HTTP server and print/display a QR code
       encoding a URL to it (http://<our-lan-ip>:<port>/connect?token=...).
    2. The phone scans the QR with its normal camera app, which opens that
       URL in its browser. That HTTP request reveals the phone's LAN IP to
       us (no companion app needed on the phone).
    3. We run `adb connect <phone-ip>:5555` and report the result back to
       the browser tab, and hand the resulting serial to the caller.

This does NOT bypass Android's own authorization prompts - the device must
already be reachable over ADB-over-network at least once before this will
work: run `adb tcpip 5555` once over USB (fixes the port at 5555 so this
keeps working across future sessions on the same Wi-Fi), or already have
Wireless debugging paired manually in Developer options.
"""
import http.server
import queue
import secrets
import socket
import subprocess
import threading
from urllib.parse import urlparse, parse_qs


def get_local_ip():
    """Best-effort LAN IP for this machine (falls back to loopback)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet actually sent, just picks a route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _adb_connect(serial, timeout=10):
    try:
        result = subprocess.run(
            ["adb", "connect", serial], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False
    out = result.stdout.lower()
    return "connected to" in out or "already connected" in out


class PairingServer:
    """
    Runs a local HTTP server and exposes a queue that yields the ADB serial
    of a device once it scans the QR and successfully connects.
    """

    def __init__(self, adb_port=5555):
        self.token = secrets.token_urlsafe(16)
        self.adb_port = adb_port
        self.ip = get_local_ip()
        self.result_queue: "queue.Queue[str]" = queue.Queue()

        self._httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), self._make_handler())
        self.port = self._httpd.server_port
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://{self.ip}:{self.port}/connect?token={self.token}"

    def start(self):
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()

    def _make_handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/connect":
                    self.send_response(404)
                    self.end_headers()
                    return

                qs = parse_qs(parsed.query)
                if qs.get("token", [None])[0] != server.token:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"Invalid or expired code.")
                    return

                phone_ip = self.client_address[0]
                serial = f"{phone_ip}:{server.adb_port}"
                ok = _adb_connect(serial)

                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if ok:
                    self.wfile.write(
                        b"<html><body style='font-family:sans-serif'>"
                        b"<h2>Connected</h2><p>You can close this tab.</p>"
                        b"</body></html>"
                    )
                    server.result_queue.put(serial)
                else:
                    self.wfile.write(
                        b"<html><body style='font-family:sans-serif'>"
                        b"<h2>Could not connect</h2>"
                        b"<p>Make sure this phone has wireless ADB debugging "
                        b"enabled and reachable (run <code>adb tcpip 5555</code> "
                        b"once over USB), then rescan.</p></body></html>"
                    )

            def log_message(self, format, *args):
                pass  # silence default request logging to stdout

        return Handler
