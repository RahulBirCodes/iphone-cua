# New iPhoneCuaEnv Architecture

This folder contains the host controller, guest agent, and async Python API
for leasing VMs and running rollouts.

## Config format

```json
{
  "instances": [
    {
      "id": "local",
      "host_controller_url": "http://127.0.0.1:7000",
      "max_concurrent": 2
    }
  ]
}
```

YAML is also supported if `PyYAML` is installed.

## Host controller

```python
from new_arch.host_controller import VMInfo, run_host_controller

vms = [
    VMInfo(vm_id="vm-1", vm_ip="192.168.64.10", agent_port=8000),
    VMInfo(vm_id="vm-2", vm_ip="192.168.64.11", agent_port=8000),
]
run_host_controller(vms, host="0.0.0.0", port=7000)
```

## Guest agent

```python
from new_arch.guest_agent import run_guest_agent

run_guest_agent(host="0.0.0.0", port=8000)
```

## Async usage

```python
import asyncio
from new_arch import IPhoneCuaPool, load_config


async def main() -> None:
    pool = IPhoneCuaPool(load_config("instances.json"), max_steps=200)
    episode = await pool.acquire()
    try:
        await episode.start_simulator()
        await episode.tap(0.5, 0.5)
        observation = await episode.observe()
        _ = observation["screenshot_b64"]
        await episode.finished()
    finally:
        await pool.release(episode)


asyncio.run(main())
```
