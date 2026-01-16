import logging
import subprocess
import time
from pathlib import Path
from typing import Tuple
from flask import Flask, jsonify, request
from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Button, Controller as MouseController
from environments.types import InvalidArgException, EnvException


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class SimController:
    def __init__(self) -> None:
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        aspect_ratio_path = Path(__file__).resolve().parent / "aspect_ratio.txt"
        try:
            aspect_ratio_str = aspect_ratio_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise EnvException(f"aspect_ratio file not found at {aspect_ratio_path}") from exc
        self.aspect_ratio = float(aspect_ratio_str)

    def _run_command(self, command: str, timeout: int = 30) -> str:
        try:
            logging.info("Executing command: %s", command)
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout_str = result.stdout.strip()
            stderr_str = result.stderr.strip()
            if result.returncode != 0:
                err_msg = (
                    f"Command '{command}' failed with exit code {result.returncode}. "
                    f"stderr: {stderr_str} stdout: {stdout_str}"
                )
                logging.error(err_msg)
                raise EnvException(err_msg)
            return stdout_str
        except EnvException:
            raise
        except Exception as exc:
            raise EnvException(f"Error while executing command: {command}") from exc

    def _get_window_geometry(self) -> Tuple[int, int, int, int]:
        """Gets the position and size of the Simulator window. Includes height of menu bar."""
        script = """
        tell application "System Events" to tell process "Simulator"
            set frontmost to true
            if not (exists (window 1)) then return "error: no window"
            set {x, y} to position of window 1
            set {w, h} to size of window 1
            return "" & x & "," & y & "," & w & "," & h
        end tell
        """
        result = self._run_command(f"osascript -e '{script}'")
        if not result or "error" in result:
            raise EnvException(f"Could not get simulator window geometry. Response: {result}")
        try:
            return tuple(map(int, result.split(",")))
        except ValueError as exc:
            raise EnvException(f"Unexpected format for window geometry: {result}") from exc

    def _translate_coords(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        if not (0.0 <= norm_x <= 1.0 and 0.0 <= norm_y <= 1.0):
            raise InvalidArgException(
                f"Normalized coordinates ({norm_x}, {norm_y}) are out of the [0.0, 1.0] bounds."
            )
        win_x, win_y, win_w, win_h = self._get_window_geometry()
        expected_content_h = win_w / self.aspect_ratio
        title_bar_h = win_h - expected_content_h
        content_origin_x = win_x
        # Offset by the calculated title bar height to reach the visible simulator content
        content_origin_y = win_y + title_bar_h
        abs_x = content_origin_x + (norm_x * win_w)
        abs_y = content_origin_y + (norm_y * expected_content_h)
        return int(abs_x), int(abs_y)

    def _handle_tap(self, action: dict) -> None:
        coords = self._translate_coords(action["x"], action["y"])
        self.mouse.position = coords
        self.mouse.click(Button.left, 1)

    def _handle_click(self, action: dict) -> None:
        coords = self._translate_coords(action["x"], action["y"])
        self.mouse.position = coords
        self.mouse.click(Button.left, action.get("count", 1))

    def _handle_swipe(self, action: dict) -> None:
        start_coords = self._translate_coords(action["start_x"], action["start_y"])
        end_coords = self._translate_coords(action["end_x"], action["end_y"])
        self.mouse.position = start_coords
        self.mouse.press(Button.left)
        time.sleep(action.get("hold_ms", 50) / 1000.0)
        self.mouse.position = end_coords
        time.sleep(action.get("release_ms", 50) / 1000.0)
        self.mouse.release(Button.left)

    def _handle_type_text(self, action: dict) -> None:
        self.keyboard.type(action["text"])

    def _handle_go_home(self, action: dict) -> None:
        self.keyboard.press(Key.cmd)
        self.keyboard.press(Key.shift)
        self.keyboard.press("h")
        self.keyboard.release("h")
        self.keyboard.release(Key.shift)
        self.keyboard.release(Key.cmd)

    def step(self, action: dict) -> None:
        action_type = action.get("type")
        if action_type == "tap":
            self._handle_tap(action)
        elif action_type == "click":
            self._handle_click(action)
        elif action_type == "swipe":
            self._handle_swipe(action)
        elif action_type == "type_text":
            self._handle_type_text(action)
        elif action_type == "go_home":
            self._handle_go_home(action)
        else:
            raise InvalidArgException(f"Unsupported action type: {action_type}")


app = Flask(__name__)
sim_controller = SimController()


@app.route("/health", methods=["GET"])
def health() -> tuple[dict, int]:
    return jsonify({"status": "OK"}), 200


@app.route("/command", methods=["POST"])
def execute_command() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    action_type = payload.get("type")
    if action_type is None:
        logging.warning("Missing 'type' in payload: %s", payload)
        return jsonify({"error": "Missing 'type' in payload"}), 400

    try:
        sim_controller.step(payload)
    except InvalidArgException as exc:
        logging.warning("Invalid action payload: %s", exc)
        return jsonify({"error": "InvalidArgException"}), 400
    except EnvException as exc:
        logging.error("Environment failure while executing action: %s", exc)
        return jsonify({"error": "EnvException"}), 500
    except Exception as exc:
        logging.exception("Unexpected error while executing command: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    return jsonify({"status": "OK"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
