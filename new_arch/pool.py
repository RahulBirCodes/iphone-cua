from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import PoolConfig
from .episode import EpisodeHandle


def _http_request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


@dataclass
class InstanceClient:
    instance_id: str
    host_controller_url: str

    def acquire_vm(self) -> dict[str, Any]:
        return _http_request_json(f"{self.host_controller_url}/acquire_vm")

    def release_vm(self, vm_id: str) -> dict[str, Any]:
        return _http_request_json(f"{self.host_controller_url}/release_vm", {"vm_id": vm_id})


class IPhoneCuaPool:
    def __init__(self, config: PoolConfig, max_steps: int = 200) -> None:
        self._instances = [
            InstanceClient(instance.instance_id, instance.host_controller_url)
            for instance in config.instances
        ]
        self._max_steps = max_steps
        total_capacity = sum(instance.max_concurrent for instance in config.instances)
        self._semaphore = asyncio.Semaphore(total_capacity)
        self._rr_index = 0

    async def acquire(self) -> EpisodeHandle:
        await self._semaphore.acquire()
        instance_count = len(self._instances)
        last_error = None
        for _ in range(instance_count):
            instance = self._instances[self._rr_index % instance_count]
            self._rr_index += 1
            try:
                response = await asyncio.to_thread(instance.acquire_vm)
            except Exception as exc:  # noqa: BLE001 - surface transport issues
                last_error = exc
                continue
            if "vm_id" in response and "vm_ip" in response:
                return EpisodeHandle(
                    vm_id=response["vm_id"],
                    vm_ip=response["vm_ip"],
                    agent_port=int(response.get("agent_port", 8000)),
                    max_steps=self._max_steps,
                    _release_cb=instance.release_vm,
                )
        self._semaphore.release()
        if last_error is not None:
            raise RuntimeError("Failed to acquire VM from any instance.") from last_error
        raise RuntimeError("No available VM from any instance.")

    async def release(self, episode: EpisodeHandle) -> None:
        await episode.close()
        self._semaphore.release()


__all__ = ["IPhoneCuaPool"]
