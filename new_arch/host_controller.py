from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class VMInfo:
    vm_id: str
    vm_ip: str
    agent_port: int


class HostControllerState:
    def __init__(self, vms: list[VMInfo]) -> None:
        self._lock = threading.Lock()
        self._free: list[VMInfo] = list(vms)
        self._leased: dict[str, VMInfo] = {}

    def acquire(self) -> VMInfo | None:
        with self._lock:
            if not self._free:
                return None
            vm = self._free.pop(0)
            self._leased[vm.vm_id] = vm
            return vm

    def release(self, vm_id: str) -> bool:
        with self._lock:
            vm = self._leased.pop(vm_id, None)
            if vm is None:
                return False
            self._free.append(vm)
            return True


class HostControllerHandler(BaseHTTPRequestHandler):
    server_version = "IPhoneCuaHostController/0.1"

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

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback naming
        if self.path == "/acquire_vm":
            self._handle_acquire()
            return
        if self.path == "/release_vm":
            self._handle_release()
            return
        self._write_json(404, {"error": "unknown_endpoint"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _handle_acquire(self) -> None:
        state: HostControllerState = self.server.state  # type: ignore[attr-defined]
        vm = state.acquire()
        if vm is None:
            self._write_json(503, {"error": "no_free_vm"})
            return
        self._write_json(
            200,
            {"vm_id": vm.vm_id, "vm_ip": vm.vm_ip, "agent_port": vm.agent_port},
        )

    def _handle_release(self) -> None:
        payload = self._read_json()
        vm_id = payload.get("vm_id")
        if not vm_id:
            self._write_json(400, {"error": "missing_vm_id"})
            return
        state: HostControllerState = self.server.state  # type: ignore[attr-defined]
        ok = state.release(str(vm_id))
        if not ok:
            self._write_json(404, {"error": "unknown_vm"})
            return
        self._write_json(200, {"status": "released"})


def run_host_controller(
    vms: list[VMInfo],
    host: str = "0.0.0.0",
    port: int = 7000,
) -> None:
    state = HostControllerState(vms)
    server = ThreadingHTTPServer((host, port), HostControllerHandler)
    server.state = state  # type: ignore[attr-defined]
    server.serve_forever()


__all__ = ["VMInfo", "run_host_controller"]
