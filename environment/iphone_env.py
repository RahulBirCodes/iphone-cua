from __future__ import annotations

import base64
import sys
import time
import uuid
from typing import Callable, Optional

import requests

import ray

from .types import RolloutResult, Turn


@ray.remote(resources={"iPhone_Slot": 1})
class iPhoneEnv:
    # vm_ip is ip available on virtual address in host
    def __init__(
        self,
        vm_template: str,
        vm_port: int = 8000,
        parse_fn: Optional[Callable[[str], Optional[dict]]] = None,
        judge_fn: Optional[Callable[[list[Turn], str, str], float]] = None,
    ):
        self._vm_template = vm_template
        self._vm_port = vm_port
        self._vm_name = f"{vm_template}-{uuid.uuid4().hex[:8]}"
        self._vm_ip = ""
        self._vm_url = ""
        self._session = requests.Session()
        self._parse_fn = parse_fn or _default_parse
        self._judge_fn = judge_fn or _default_judge
        self._create_vm()

    def collect_rollout(
        self, task_id: str, task_prompt: str, max_turns: int
    ) -> RolloutResult:
        turns: list[Turn] = []
        try:
            turns.append(
                Turn(
                    role="environment",
                    screenshot=None,
                    raw_output=task_prompt,
                    action=None,
                    reward=0.0,
                )
            )
            screenshot = self._reset()
            turns.append(
                Turn(
                    role="environment",
                    screenshot=screenshot,
                    raw_output=None,
                    action=None,
                    reward=0.0,
                )
            )
            for _ in range(max_turns):
                raw_output = self._get_action(screenshot)
                parsed_action = self._parse_fn(raw_output)
                parse_reward = 0.0
                if parsed_action is None:
                    parse_reward = -0.1
                    parsed_action = {"action": "observe", "params": {}}
                turns.append(
                    Turn(
                        role="agent",
                        screenshot=None,
                        raw_output=raw_output,
                        action=parsed_action,
                        reward=parse_reward,
                    )
                )
                if _is_done_action(parsed_action):
                    break
                screenshot, env_error = self._step(parsed_action)
                turns.append(
                    Turn(
                        role="environment",
                        screenshot=screenshot,
                        raw_output=env_error,
                        action=None,
                        reward=0.0,
                    )
                )
            judge_reward = self._judge_fn(turns, task_id, task_prompt)
            turns.append(
                Turn(
                    role="environment",
                    screenshot=None,
                    raw_output="judge",
                    action=None,
                    reward=judge_reward,
                )
            )
            return RolloutResult(turns=turns, task_id=task_id)
        except Exception:
            sys.exit(1)

    def _reset(self) -> bytes:
        _, error = self._send_action("reset", {})
        if error:
            raise RuntimeError(error)
        screenshot, error = self._send_action("observe", {})
        if error:
            raise RuntimeError(error)
        return screenshot

    def _step(self, action: dict) -> tuple[bytes, str | None]:
        if not isinstance(action, dict):
            raise ValueError("action must be a dict")
        action_name = str(action.get("action", "")).strip()
        params = action.get("params", {})
        if not action_name:
            raise ValueError("action missing name")
        if not isinstance(params, dict):
            params = {}
        screenshot, error = self._send_action(action_name, params)
        return screenshot, error

    def _send_action(self, action: str, params: dict) -> tuple[bytes, str | None]:
        try:
            resp = self._session.post(
                f"{self._vm_url}/action",
                json={"action": action, "params": params},
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"connection_error:{exc}") from exc
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"invalid_response:{exc}") from exc
        error = data.get("error")
        screenshot_b64 = data.get("screenshot_b64", "")
        return _decode_screenshot(screenshot_b64), error

    def _check_heartbeat(self) -> bool:
        try:
            resp = self._session.get(f"{self._vm_url}/heartbeat", timeout=2)
            data = resp.json()
        except Exception:
            return False
        return data.get("status") == "ok"

    def _ensure_vm_running(self) -> None:
        if self._check_heartbeat():
            return
        raise RuntimeError("vm is not reachable")

    def _create_vm(self) -> None:
        try:
            subprocess.run(
                ["tart", "clone", self._vm_template, self._vm_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            subprocess.Popen(
                ["tart", "run", self._vm_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._vm_ip = _wait_for_vm_ip(self._vm_name)
            self._vm_url = f"http://{self._vm_ip}:{self._vm_port}"
            deadline = time.time() + 120
            while time.time() < deadline:
                if self._check_heartbeat():
                    return
                time.sleep(2)
            raise RuntimeError("vm did not become healthy")
        except Exception:
            sys.exit(1)

    def _get_action(self, screenshot: bytes) -> str:
        _ = screenshot
        return ""


def _decode_screenshot(b64_str: str) -> bytes:
    if not b64_str:
        return b""
    if b64_str.startswith("data:"):
        _, _, b64_str = b64_str.partition(",")
    try:
        return base64.b64decode(b64_str)
    except Exception:
        return b""

def _wait_for_vm_ip(vm_name: str) -> str:
    deadline = time.time() + 60
    while time.time() < deadline:
        vm_ip = _get_vm_ip(vm_name)
        if vm_ip:
            return vm_ip
        time.sleep(2)
    raise RuntimeError("unable to fetch vm ip")


def _get_vm_ip(vm_name: str) -> str | None:
    result = subprocess.run(
        ["tart", "ip", vm_name],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return None
    ip = result.stdout.strip()
    return ip or None


def _default_parse(raw_output: str) -> Optional[dict]:
    _ = raw_output
    return {"action": "observe", "params": {}}


def _default_judge(turns: list[Turn], task_id: str, task_prompt: str) -> float:
    _ = turns
    _ = task_id
    _ = task_prompt
    return 0.0


def _is_done_action(action: dict) -> bool:
    action_name = str(action.get("action", "")).strip().lower()
    return action_name in {"done", "finish", "stop"}
