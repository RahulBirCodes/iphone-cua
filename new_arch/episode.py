from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from typing import Any


def _http_request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _http_get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


@dataclass
class EpisodeHandle:
    vm_id: str
    vm_ip: str
    agent_port: int
    max_steps: int
    _release_cb: Any
    _step_count: int = 0
    _closed: bool = False

    @property
    def base_url(self) -> str:
        return f"http://{self.vm_ip}:{self.agent_port}"

    async def _post(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("EpisodeHandle is closed.")
        self._increment_steps()
        url = f"{self.base_url}{endpoint}"
        return await asyncio.to_thread(_http_request_json, url, payload)

    async def _get(self, endpoint: str) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("EpisodeHandle is closed.")
        self._increment_steps()
        url = f"{self.base_url}{endpoint}"
        return await asyncio.to_thread(_http_get_json, url)

    def _increment_steps(self) -> None:
        self._step_count += 1
        if self._step_count > self.max_steps:
            raise RuntimeError("Episode exceeded max_steps.")

    async def tap(self, x: float, y: float) -> None:
        await self._post("/tap", {"x": x, "y": y})

    async def swipe(self, x1: float, y1: float, x2: float, y2: float) -> None:
        await self._post("/swipe", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    async def type_text(self, text: str) -> None:
        await self._post("/type_text", {"text": text})

    async def go_home(self) -> None:
        await self._post("/go_home")

    async def wait(self, seconds: float = 0.5) -> None:
        await self._post("/wait", {"seconds": seconds})

    async def fail(self) -> None:
        await self._post("/fail")

    async def finished(self) -> None:
        await self._post("/finished")

    async def reset(self) -> None:
        await self._post("/reset_simulator")

    async def start_simulator(self) -> None:
        await self._post("/start_simulator")

    async def observe(self) -> dict[str, Any]:
        return await self._get("/observe")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._release_cb, self.vm_id)


__all__ = ["EpisodeHandle"]
