from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
import requests
import ray
from .policy import PolicyBackend
from .schemas import (
    EnvRuntimeError,
    RewardPolicy,
    RolloutResult,
    TerminationReason,
    Turn,
)

CONTROLLER_PORT = 8000
VM_IP_TIMEOUT = 60
VM_PORT_TIMEOUT = 60


@ray.remote(resources={"iphone_slot": 1}, max_restarts=3)
class iPhoneEnv:
    # vm_ip is ip available on virtual address in host
    def __init__(
        self,
        base_image: str,
        policy_backend: PolicyBackend,
        sampling: dict[str, Any],
        parse_fn: Callable[[str], dict | None],
        judge_fn: Callable[[list[Turn], str, str], bool],
        reward_policy: RewardPolicy,
    ):
        self._base_image = base_image
        self._session = requests.Session()
        self._policy_backend = policy_backend
        self._sampling = sampling
        self._parse_fn = parse_fn
        self._judge_fn = judge_fn
        self._reward_policy = reward_policy
        self.create_vm()

    def collect_rollout(
        self,
        system_prompt: str,
        task_id: str,
        task_prompt: str,
        max_turns: int,
        save_json: bool = False,
        save_path: str | None = None,
    ) -> RolloutResult:
        turns: list[Turn] = []
        termination_reason: TerminationReason | None = None
        cumulative_reward: float = 0.0
        last_screenshot = ""
        pending_user_text = ""

        try:
            system_turn = Turn(
                t=len(turns),
                role="system",
                screenshot=None,
                reasoning=None,
                content=system_prompt,
                action=None,
                reward=None,
            )
            turns.append(system_turn)

            user_turn = Turn(
                t=len(turns),
                role="user",
                screenshot=None,
                reasoning=None,
                content=task_prompt,
                action=None,
                reward=None,
            )
            turns.append(user_turn)

            _, reset_error = self._send_action("reset", {})
            if reset_error:
                raise EnvRuntimeError(reset_error)

            for _ in range(max_turns):
                screenshot, observe_error = self._send_action("observe", {})
                if observe_error:
                    raise EnvRuntimeError(observe_error)
                if screenshot:
                    last_screenshot = screenshot

                observation_turn = Turn(
                    t=len(turns),
                    role="user",
                    screenshot=last_screenshot,
                    reasoning=None,
                    content=pending_user_text,
                    action=None,
                    reward=None,
                )
                turns.append(observation_turn)
                pending_user_text = ""

                output = self._policy_backend.generate(turns, self._sampling)
                reasoning = output.reasoning
                content = output.content

                parsed_action = self._parse_fn(content)
                termination = _terminal_reason(parsed_action)

                if parsed_action is None:
                    parse_penalty = self._reward_policy.parse_penalty
                    cumulative_reward += parse_penalty
                    assistant_turn = Turn(
                        t=len(turns),
                        role="assistant",
                        screenshot=None,
                        reasoning=reasoning,
                        content=content,
                        action=None,
                        reward=parse_penalty,
                    )
                    turns.append(assistant_turn)

                    pending_user_text = _build_user_text(
                        error="parse_error",
                        error_type="parse_error",
                    )
                    continue

                assistant_turn = Turn(
                    t=len(turns),
                    role="assistant",
                    screenshot=None,
                    reasoning=reasoning,
                    content=content,
                    action=parsed_action,
                    reward=None,
                )
                turns.append(assistant_turn)

                if termination is not None:
                    termination_reason = termination
                    break

                _, env_error = self._step(parsed_action)
                pending_user_text = _build_user_text(
                    error=env_error,
                    error_type="env_error",
                )
            else:
                termination_reason = TerminationReason.TRUNCATED

            final_reward: float
            if termination_reason == TerminationReason.DONE:
                success = self._judge_fn(turns, task_id, task_prompt)
                if success:
                    final_reward = self._reward_policy.success_reward
                else:
                    final_reward = self._reward_policy.failure_penalty
            elif termination_reason == TerminationReason.FAIL:
                final_reward = self._reward_policy.failure_penalty
            elif termination_reason == TerminationReason.TRUNCATED:
                final_reward = self._reward_policy.failure_penalty

            total_reward = cumulative_reward + final_reward

            result = RolloutResult(
                turns=turns,
                task_id=task_id,
                termination_reason=termination_reason,
                final_reward=total_reward,
            )
            if save_json:
                self._save_rollout_json(result, save_path)
            return result
        except EnvRuntimeError:
            raise
        except Exception:
            sys.exit(1)

    def _step(self, action: dict) -> tuple[str, str | None]:
        action_name = action["action"]
        params = action.get("params", {})
        return self._send_action(action_name, params)

    def _send_action(self, action: str, params: dict) -> tuple[str, str | None]:
        try:
            resp = self._session.post(
                f"http://{self._vm_ip}:{self._vm_port}/action",
                json={"action": action, "params": params},
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"connection_error:{exc}") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(f"http_error:{resp.status_code}")
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"invalid_response:{exc}") from exc
        error = data.get("error")
        screenshot_b64 = data.get("screenshot_b64", "")
        if not isinstance(screenshot_b64, str):
            screenshot_b64 = ""
        return _normalize_screenshot_b64(screenshot_b64), error

    def create_vm(self) -> None:
        actor_id = ray.get_runtime_context().get_actor_id()
        vm_name = f"rollout-{actor_id}"
        subprocess.run(["tart", "delete", vm_name], capture_output=True)
        # should be local img
        subprocess.run(["tart", "clone", self._base_image, vm_name], check=True)
        subprocess.Popen(["tart", "run", vm_name, "--no-graphics"])
        vm_ip = self._wait_for_ip(vm_name, timeout=VM_IP_TIMEOUT)
        self._wait_for_heartbeat(vm_ip, CONTROLLER_PORT, timeout=VM_PORT_TIMEOUT)

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

    def _wait_for_heartbeat(self, ip: str, port: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        heartbeat_url = f"http://{ip}:{port}/heartbeat"
        while time.monotonic() < deadline:
            try:
                resp = self._session.get(heartbeat_url, timeout=1)
                if resp.status_code != 200:
                    time.sleep(0.5)
                    continue
                payload = resp.json()
                if payload.get("status") == "ok":
                    return
            except (requests.exceptions.RequestException, ValueError):
                # Server not up yet or invalid heartbeat payload.
                time.sleep(0.5)
        raise RuntimeError(
            f"Controller heartbeat at {heartbeat_url} not ready within {timeout}s"
        )

    def _save_rollout_json(self, result: RolloutResult, save_path: str | None) -> None:
        if save_path:
            path = Path(save_path)
        else:
            path = Path("rollouts") / f"{result.task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _rollout_to_dict(result)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _normalize_screenshot_b64(b64_str: str) -> str:
    if not b64_str:
        return ""
    if b64_str.startswith("data:"):
        _, _, b64_str = b64_str.partition(",")
    return b64_str


def _terminal_reason(action: dict | None) -> TerminationReason | None:
    if not action:
        return None
    action_name = str(action.get("action", "")).strip().lower()
    if action_name in {"finished", "finish", "done", "stop"}:
        return TerminationReason.DONE
    if action_name in {"fail", "failed"}:
        return TerminationReason.FAIL
    return None


def _build_user_text(
    error: str | None = None,
    error_type: str | None = None,
) -> str:
    if not error:
        return ""
    feedback = {"type": error_type or "env_error", "message": error}
    payload = {"feedback": feedback}
    return "ENVIRONMENT RESPONSE:\n" + json.dumps(payload, ensure_ascii=True)


def _rollout_to_dict(result: RolloutResult) -> dict[str, Any]:
    payload = asdict(result)
    termination_reason = payload.get("termination_reason")
    if isinstance(termination_reason, TerminationReason):
        payload["termination_reason"] = termination_reason.value
    return payload
