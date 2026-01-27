# Ray-Based iPhone CUA Environment Implementation Plan

## Overview

Refactor the environment folder to use Ray actors. One actor per VM, resources dict for host pinning.

## Files to Delete

- `environment/host_controller.py`
- `environment/tests/test_host_controller.py`

## Files to Modify/Create

### 1. `vm_controller.py` - Add Heartbeat Endpoint

Add `do_GET` handler with `/heartbeat`:

```python
def do_GET(self) -> None:
    if self.path == "/heartbeat":
        self._write_json(200, {"status": "ok"})
        return
    self._write_json(404, {"error": "not_found"})
```

### 2. `types.py` - Update Data Types

**Remove:** `HostInfo`, `VMLease`

**Add:**

```python
@dataclass
class Turn:
    role: str                    # "agent" | "environment"
    screenshot: bytes | None     # environment provides
    raw_output: str | None       # agent's full response (reasoning + action)
    action: dict | None          # parsed action from raw_output
    reward: float

@dataclass
class RolloutResult:
    turns: list[Turn]
    task_id: str
```

### 3. `iphone_env.py` - Ray Actor

```python
@ray.remote
class iPhoneEnv:
    def __init__(self, vm_name: str, vm_ip: str, vm_port: int = 8000):
        self._vm_name = vm_name
        self._vm_ip = vm_ip
        self._vm_port = vm_port
        self._ensure_vm_running()  # Check heartbeat, spawn VM if needed

    def collect_rollout(self, task_id: str, max_turns: int) -> RolloutResult:
        # Reset -> loop (get_action, step) -> return RolloutResult
        # If VM dies, actor crashes - Ray restarts it

    def _reset(self) -> bytes:
        # VMController reset + observe

    def _step(self, action: dict) -> bytes:
        # Execute action, return screenshot bytes

    def _send_action(self, action: str, params: dict) -> tuple[bytes, str | None]:
        # HTTP POST /action

    def _check_heartbeat(self) -> bool:
        # GET /heartbeat

    def _ensure_vm_running(self) -> None:
        # If heartbeat fails: tart run <vm_name>, update _vm_ip

    def _get_action(self, screenshot: bytes) -> tuple[str, dict]:
        # Empty stub - returns (raw_output, parsed_action)
        pass
```

**Usage with resources dict:**

```python
# Pin actor to specific host
env = iPhoneEnv.options(
    resources={"host1": 1},
    max_restarts=-1,
).remote(vm_name="iphone-vm-1", vm_ip="192.168.64.2")

# Collect rollout
result = ray.get(env.collect_rollout.remote(task_id="task-123", max_turns=10))
```

## Implementation Sequence

1. Delete `host_controller.py` and `tests/test_host_controller.py`
2. Add `/heartbeat` GET endpoint to `vm_controller.py`
3. Update `types.py` - add `Turn`, `RolloutResult`, remove old types
4. Rewrite `iphone_env.py` as Ray actor

## Key Points

- Actor crashes on VM failure (no retry) - Ray handles restart via `max_restarts=-1`. get rollout is atomic, if it fails, the actor should fail (sys.exit(1)) and then should restart and the init will restart the vm.
- use resources to tie a specific vm with a specific host since a certain actor with a vm ip only works on the host it's on (vm has virtual address on that host).
- `__init__` ensures VM is running before any work
- `Turn.role` = "agent" | "environment" for token masking later
- `_get_action()` is empty stub for now
- Resources dict pins actor to host: `resources={"host1": 1}`

## Verification

Run with a local VM to test basic rollout collection.
