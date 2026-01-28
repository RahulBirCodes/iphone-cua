from __future__ import annotations

import socket
import subprocess
import sys
import time
from typing import Callable, Optional

import requests

import ray

CONTROLLER_PORT = 8000
VM_IP_TIMEOUT = 60
VM_PORT_TIMEOUT = 60

from .types import RolloutResult, Turn

ParseResult = dict | tuple[dict, float]


@ray.remote(resources={"iphone_slot": 1})
class iPhoneEnv:
    # vm_ip is ip available on virtual address in host
    def __init__(
        self,
        base_image: str,
        parse_fn: Optional[Callable[[str], Optional[ParseResult]]] = None,
        judge_fn: Optional[Callable[[list[Turn], str, str], float]] = None,
    ):
        self._base_image = base_image
        self._session = requests.Session()
        self._parse_fn = parse_fn or _default_parse
        self._judge_fn = judge_fn or _default_judge
        self.create_vm()

    def collect_rollout(
        self,
        system_prompt: str,
        task_id: str,
        task_prompt: str,
        max_turns: int,
    ) -> RolloutResult:
        turns: list[Turn] = []
        try:
            turns.append(
                Turn(
                    role="system",
                    screenshot=None,
                    raw_output=system_prompt,
                    action=None,
                    reward=None,
                )
            )
            turns.append(
                Turn(
                    role="user",
                    screenshot=None,
                    raw_output=task_prompt,
                    action=None,
                    reward=None,
                )
            )
            screenshot = self._reset()
            turns.append(
                Turn(
                    role="user",
                    screenshot=screenshot,
                    raw_output=None,
                    action=None,
                    reward=None,
                )
            )
            for _ in range(max_turns):
                raw_output = self._get_action(screenshot)
                parsed_action: dict = {"action": "observe", "params": {}}
                parse_reward = 0.0
                parse_result = self._parse_fn(raw_output)
                if parse_result is None:
                    parse_reward = -0.1
                elif isinstance(parse_result, tuple):
                    parsed_action, parse_reward = parse_result
                    if not isinstance(parsed_action, dict):
                        parsed_action = {"action": "observe", "params": {}}
                        parse_reward = -0.1
                elif isinstance(parse_result, dict):
                    parsed_action = parse_result
                else:
                    parse_reward = -0.1
                assistant_reward = parse_reward
                if _is_done_action(parsed_action):
                    assistant_reward = self._judge_fn(turns, task_id, task_prompt)
                turns.append(
                    Turn(
                        role="assistant",
                        screenshot=None,
                        raw_output=raw_output,
                        action=parsed_action,
                        reward=assistant_reward,
                    )
                )
                if _is_done_action(parsed_action):
                    break
                screenshot, env_error = self._step(parsed_action)
                turns.append(
                    Turn(
                        role="user",
                        screenshot=screenshot,
                        raw_output=env_error,
                        action=None,
                        reward=None,
                    )
                )
            return RolloutResult(turns=turns, task_id=task_id)
        except Exception:
            sys.exit(1)

    def _reset(self) -> str:
        _, error = self._send_action("reset", {})
        if error:
            raise RuntimeError(error)
        screenshot, error = self._send_action("observe", {})
        if error:
            raise RuntimeError(error)
        return screenshot

    def _step(self, action: dict) -> tuple[str, str | None]:
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

    def _send_action(self, action: str, params: dict) -> tuple[str, str | None]:
        try:
            resp = self._session.post(
                f"http://{self._vm_ip}:{self._vm_port}/action",
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
        if not isinstance(screenshot_b64, str):
            screenshot_b64 = ""
        return _normalize_screenshot_b64(screenshot_b64), error

    def _check_heartbeat(self) -> bool:
        try:
            resp = self._session.get(
                f"http://{self._vm_ip}:{self._vm_port}/heartbeat", timeout=2
            )
            data = resp.json()
        except Exception:
            return False
        return data.get("status") == "ok"

    def _ensure_vm_running(self) -> None:
        if self._check_heartbeat():
            return
        raise RuntimeError("vm is not reachable")

    def create_vm(self) -> None:
        actor_id = ray.get_runtime_context().get_actor_id()
        vm_name = f"rollout-{actor_id}"
        subprocess.run(["tart", "delete", vm_name], capture_output=True)
        # should be local img
        subprocess.run(["tart", "clone", self._base_image, vm_name], check=True)
        subprocess.Popen(["tart", "run", vm_name, "--no-graphics"])
        vm_ip = self._wait_for_ip(vm_name, timeout=VM_IP_TIMEOUT)
        self._wait_for_port(vm_ip, CONTROLLER_PORT, timeout=VM_PORT_TIMEOUT)

        self._vm_id = vm_name
        self._vm_ip = vm_ip
        self._vm_port = CONTROLLER_PORT

    def _wait_for_ip(self, vm_name: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["tart", "ip", vm_name], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            time.sleep(0.5)
        raise RuntimeError(f"VM {vm_name} did not get IP within {timeout}s")

    def _wait_for_port(self, ip: str, port: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((ip, port), timeout=1):
                    return
            except (socket.timeout, ConnectionRefusedError, OSError):
                time.sleep(0.5)
        raise RuntimeError(f"Controller at {ip}:{port} not ready within {timeout}s")

    def _get_action(self, screenshot: str) -> str:
        _ = screenshot
        return ""


def _normalize_screenshot_b64(b64_str: str) -> str:
    if not b64_str:
        return ""
    if b64_str.startswith("data:"):
        _, _, b64_str = b64_str.partition(",")
    return b64_str


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
