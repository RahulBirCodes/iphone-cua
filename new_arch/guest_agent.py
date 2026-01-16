from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pynput import keyboard, mouse


class GuestAgentHandler(BaseHTTPRequestHandler):
    server_version = "IPhoneCuaGuestAgent/0.1"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        payload = self.rfile.read(length)
        return json.loads(payload.decode("utf-8"))

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback naming
        handlers = {
            "/tap": self._handle_tap,
            "/swipe": self._handle_swipe,
            "/type_text": self._handle_type_text,
            "/go_home": self._handle_go_home,
            "/wait": self._handle_wait,
            "/fail": self._handle_terminal,
            "/finished": self._handle_terminal,
            "/reset_simulator": self._handle_reset,
            "/start_simulator": self._handle_start_simulator,
            "/observe": self._handle_observe,
        }
        handler = handlers.get(self.path)
        if handler is None:
            self._write_json(404, {"error": "unknown_endpoint"})
            return
        handler()

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback naming
        if self.path == "/observe":
            self._handle_observe()
            return
        self._write_json(404, {"error": "unknown_endpoint"})

    def _handle_tap(self) -> None:
        payload = self._read_json()
        x = float(payload.get("x", 0.0))
        y = float(payload.get("y", 0.0))
        abs_x, abs_y = _normalized_to_screen(x, y)
        controller = mouse.Controller()
        controller.position = (abs_x, abs_y)
        controller.click(mouse.Button.left, 1)
        self._write_json(200, {"status": "ok"})

    def _handle_swipe(self) -> None:
        payload = self._read_json()
        x1 = float(payload.get("x1", 0.0))
        y1 = float(payload.get("y1", 0.0))
        x2 = float(payload.get("x2", 0.0))
        y2 = float(payload.get("y2", 0.0))
        start_x, start_y = _normalized_to_screen(x1, y1)
        end_x, end_y = _normalized_to_screen(x2, y2)
        controller = mouse.Controller()
        controller.position = (start_x, start_y)
        controller.press(mouse.Button.left)
        time.sleep(0.05)
        controller.position = (end_x, end_y)
        time.sleep(0.05)
        controller.release(mouse.Button.left)
        self._write_json(200, {"status": "ok"})

    def _handle_type_text(self) -> None:
        payload = self._read_json()
        text = str(payload.get("text", ""))
        controller = keyboard.Controller()
        controller.type(text)
        self._write_json(200, {"status": "ok"})

    def _handle_go_home(self) -> None:
        controller = keyboard.Controller()
        with controller.pressed(keyboard.Key.cmd):
            with controller.pressed(keyboard.Key.shift):
                controller.press("h")
                controller.release("h")
        self._write_json(200, {"status": "ok"})

    def _handle_wait(self) -> None:
        payload = self._read_json()
        seconds = float(payload.get("seconds", 0.5))
        time.sleep(seconds)
        self._write_json(200, {"status": "ok"})

    def _handle_terminal(self) -> None:
        self._write_json(200, {"status": "ok"})

    def _handle_reset(self) -> None:
        _run_command(["xcrun", "simctl", "erase", "all"])
        self._write_json(200, {"status": "reset"})

    def _handle_start_simulator(self) -> None:
        _run_command(["open", "-a", "Simulator"])
        self._write_json(200, {"status": "started"})

    def _handle_observe(self) -> None:
        screenshot_b64 = _capture_screen_base64()
        self._write_json(200, {"screenshot_b64": screenshot_b64})


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_SCREEN_CACHE: tuple[int, int] | None = None


def _screen_size() -> tuple[int, int]:
    global _SCREEN_CACHE
    if _SCREEN_CACHE is not None:
        return _SCREEN_CACHE
    result = subprocess.run(
        ["system_profiler", "SPDisplaysDataType", "-json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    width = 1920
    height = 1080
    try:
        data = json.loads(result.stdout)
        displays = data.get("SPDisplaysDataType", [])
        for display in displays:
            resolution = display.get("_spdisplays_resolution")
            if isinstance(resolution, str) and " x " in resolution:
                parts = resolution.split(" x ")
                width = int(parts[0])
                height = int(parts[1].split(" ")[0])
                break
    except (ValueError, KeyError, TypeError):
        pass
    _SCREEN_CACHE = (width, height)
    return width, height


def _normalized_to_screen(x: float, y: float) -> tuple[int, int]:
    width, height = _screen_size()
    abs_x = max(0, min(width - 1, int(x * width)))
    abs_y = max(0, min(height - 1, int(y * height)))
    return abs_x, abs_y


def _capture_screen_base64() -> str:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "screen.png"
        _run_command(["screencapture", "-x", "-t", "png", str(path)])
        data = path.read_bytes() if path.exists() else b""
    return base64.b64encode(data).decode("ascii")


def run_guest_agent(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), GuestAgentHandler)
    server.serve_forever()


__all__ = ["run_guest_agent"]
