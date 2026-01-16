from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from pynput import keyboard, mouse


class VMController:
    """HTTP-exposed controller for simulator actions and observations.

    Allowed actions (from `new_arch/guest_agent.py`):
        - tap: {x, y}
        - swipe: {x1, y1, x2, y2}
        - type_text: {text}
        - go_home: {}
        - wait: {seconds}
        - reset: {}
        - set_device: {device_name}
        - observe: {}

    Request format (POST /action):
        {"action": "<name>", "params": {...}}

    Response format:
        {"screenshot_b64": "<base64>", "error": "<message or null>"}
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        self.host = host
        self.port = port
        self._device_name = "iPhone-17-Pro"
        self._device_udids: Dict[str, str] = {}
        self._device_aspect_ratios: Dict[str, float] = {}
        self._handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "tap": self._handle_tap,
            "swipe": self._handle_swipe,
            "type_text": self._handle_type_text,
            "go_home": self._handle_go_home,
            "wait": self._handle_wait,
            "reset": self._handle_reset,
            "set_device": self._handle_set_device,
            "observe": self._handle_observe,
        }

    def run(self) -> None:
        """Start the HTTP server and block forever."""
        server = ThreadingHTTPServer((self.host, self.port), _VMControllerHandler)
        server.controller = self
        server.serve_forever()

    def handle_action(self, action: str, params: Dict[str, Any]) -> None:
        """Execute the requested action or raise ValueError for invalid actions."""
        handler = self._handlers.get(action)
        if handler is None:
            raise ValueError(f"unknown_action:{action}")
        handler(params)

    def _handle_tap(self, params: Dict[str, Any]) -> None:
        x = float(params.get("x", 0.0))
        y = float(params.get("y", 0.0))
        abs_x, abs_y = self._normalized_to_screen(x, y)
        controller = mouse.Controller()
        controller.position = (abs_x, abs_y)
        time.sleep(0.02)
        controller.press(mouse.Button.left)
        time.sleep(0.02)
        controller.release(mouse.Button.left)

    def _handle_swipe(self, params: Dict[str, Any]) -> None:
        x1 = float(params.get("x1", 0.0))
        y1 = float(params.get("y1", 0.0))
        x2 = float(params.get("x2", 0.0))
        y2 = float(params.get("y2", 0.0))
        start_x, start_y = self._normalized_to_screen(x1, y1)
        end_x, end_y = self._normalized_to_screen(x2, y2)
        controller = mouse.Controller()
        controller.position = (start_x, start_y)
        controller.press(mouse.Button.left)
        time.sleep(0.05)
        controller.position = (end_x, end_y)
        time.sleep(0.05)
        controller.release(mouse.Button.left)

    def _handle_type_text(self, params: Dict[str, Any]) -> None:
        text = str(params.get("text", ""))
        controller = keyboard.Controller()
        controller.type(text)

    def _handle_go_home(self, params: Dict[str, Any]) -> None:
        _ = params
        controller = keyboard.Controller()
        with controller.pressed(keyboard.Key.cmd):
            with controller.pressed(keyboard.Key.shift):
                controller.press("h")
                controller.release("h")

    def _handle_wait(self, params: Dict[str, Any]) -> None:
        seconds = float(params.get("seconds", 0.5))
        time.sleep(seconds)

    def _handle_reset(self, params: Dict[str, Any]) -> None:
        _ = params
        udid = self._get_or_create_device_udid()
        _run_command_allow_fail(["xcrun", "simctl", "shutdown", udid])
        _run_command(["xcrun", "simctl", "erase", udid], timeout=300)
        _run_command(["xcrun", "simctl", "boot", udid], timeout=300)
        _run_command(["xcrun", "simctl", "bootstatus", udid, "-b"], timeout=300)
        # Bezels are handled in the base VM image.

    def _handle_set_device(self, params: Dict[str, Any]) -> None:
        device_name = str(params.get("device_name", "")).strip()
        if not device_name:
            return
        self._device_name = device_name

    def _handle_observe(self, params: Dict[str, Any]) -> None:
        _ = params

    def _get_window_geometry(self) -> Tuple[int, int, int, int]:
        script = (
            'tell application "System Events" to tell process "Simulator"\n'
            "    set frontmost to true\n"
            '    if not (exists (window 1)) then return "error: no window"\n'
            "    set {x, y} to position of window 1\n"
            "    set {w, h} to size of window 1\n"
            '    return "" & x & "," & y & "," & w & "," & h\n'
            "end tell"
        )
        result = _run_command(["osascript", "-e", script])
        if not result or "error" in result:
            raise RuntimeError(f"could not get simulator window geometry: {result}")
        try:
            return tuple(map(int, result.split(",")))
        except ValueError as exc:
            raise RuntimeError(f"unexpected window geometry format: {result}") from exc

    def _normalized_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"normalized coordinates out of bounds: ({x}, {y})")
        win_x, win_y, win_w, win_h = self._get_window_geometry()
        aspect_ratio = self._get_aspect_ratio()
        expected_content_h = win_w / aspect_ratio
        title_bar_h = win_h - expected_content_h
        content_origin_x = win_x
        content_origin_y = win_y + title_bar_h
        abs_x = content_origin_x + (x * win_w)
        abs_y = content_origin_y + (y * expected_content_h)
        return int(abs_x), int(abs_y)

    def _get_aspect_ratio(self) -> float:
        cached = self._device_aspect_ratios.get(self._device_name)
        if cached is not None:
            return cached
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "sim.png"
            _capture_simulator_png(path)
            aspect_ratio = _read_png_aspect_ratio(path)
        self._device_aspect_ratios[self._device_name] = aspect_ratio
        return aspect_ratio

    def _get_or_create_device_udid(self) -> str:
        device_udid = self._device_udids[self._device_name]
        if device_udid is not None:
            return device_udid
        udid = _find_first_device_udid(self._device_name)
        if udid is None:
            udid = _run_command(
                [
                    "xcrun",
                    "simctl",
                    "create",
                    "main",
                    f"com.apple.CoreSimulator.SimDeviceType.{self._device_name}",
                ],
                timeout=300,
            ).strip()
        if not udid:
            raise RuntimeError("failed to create simulator device")
        self._device_udids[self._device_name] = udid
        return udid


class _VMControllerHandler(BaseHTTPRequestHandler):
    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        payload = self.rfile.read(length)
        return json.loads(payload.decode("utf-8"))

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        if self.path != "/action":
            self._write_json(404, {"screenshot_b64": "", "error": "unknown_endpoint"})
            return
        payload = self._read_json()
        action = str(payload.get("action", ""))
        params = payload.get("params", {})
        if not isinstance(params, dict):
            params = {}
        error: Optional[str] = None
        screenshot_b64 = ""
        try:
            self.server.controller.handle_action(action, params)
            screenshot_b64 = _capture_simulator_base64()
        except Exception as exc:
            error = str(exc)
        self._write_json(200, {"screenshot_b64": screenshot_b64, "error": error})


def _run_command(command: list[str], timeout: int = 30) -> str:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(command)} (exit {result.returncode}) stderr: {stderr} stdout: {stdout}"
        )
    return stdout


def _run_command_allow_fail(command: list[str]) -> None:
    subprocess.run(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _capture_simulator_png(path: Path) -> None:
    _run_command(["xcrun", "simctl", "io", "booted", "screenshot", str(path)])


def _capture_simulator_base64() -> str:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "screen.png"
        resized_path = Path(tmp_dir) / "screen_768.png"
        _capture_simulator_png(path)
        _run_command(
            ["sips", "--resampleWidth", "768", str(path), "--out", str(resized_path)]
        )
        data = resized_path.read_bytes() if resized_path.exists() else b""
    return base64.b64encode(data).decode("ascii")


def _read_png_aspect_ratio(path: Path) -> float:
    output = _run_command(
        [
            "sips",
            "-g",
            "pixelWidth",
            "-g",
            "pixelHeight",
            str(path),
        ]
    )
    width = None
    height = None
    for line in output.splitlines():
        if "pixelWidth" in line:
            _, value = line.split(":", 1)
            width = int(value.strip())
        elif "pixelHeight" in line:
            _, value = line.split(":", 1)
            height = int(value.strip())
    if width is None or height is None or height <= 0:
        raise RuntimeError(f"could not parse screenshot dimensions from sips: {output}")
    return width / height


def _find_first_device_udid(device_name: str) -> Optional[str]:
    output = _run_command(["xcrun", "simctl", "list", "devices", "-j"])
    data = json.loads(output)
    devices = data.get("devices", {})
    for _, entries in devices.items():
        for entry in entries:
            if entry.get("isAvailable") is True and entry.get("name") == device_name:
                udid = entry.get("udid")
                if udid:
                    return udid
    return None
