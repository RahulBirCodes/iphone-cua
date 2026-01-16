# File Guide

This folder implements the new host/guest/async RL environment architecture.

## High-level system design

The system is split into three layers to maximize parallelism and keep per-step
latency low:

1) Instance (Host Machine)
   - Runs a Host Controller that manages a pool of guest VMs.
   - Handles VM lifecycle and leasing only (no per-step action routing).

2) Guest VM (macOS Virtual Machine)
   - Runs an iOS Simulator and a persistent Guest Agent HTTP server.
   - Injects UI input using CGEvents via `pynput`.
   - Resets per episode with `xcrun simctl erase all` (no VM snapshots).

3) RL Environment Interface (Python)
   - Manages async VM leasing and per-step actions directly to Guest Agents.
   - One rollout maps to one VM lease, enabling parallel rollouts.

This layout avoids cloud-provider assumptions, keeps resets fast, and ensures
actions go straight to the VM (lowest latency path).

## Core abstractions

- `IPhoneCuaPool`
  - Global pool across all instances.
  - Uses a semaphore for concurrency and round-robin instance selection.
  - `acquire()` leases a VM and returns an `EpisodeHandle`.

- `EpisodeHandle`
  - Represents a single rollout bound to one guest VM.
  - Sends HTTP actions directly to the Guest Agent.
  - Enforces `max_steps` and exposes methods like `tap`, `swipe`, `observe`,
    `reset`, `finished`, and `close`.

- Host Controller (HTTP service)
  - Tracks free vs. leased VMs.
  - Returns `{ vm_id, vm_ip, agent_port }` on acquire.
  - Releases VM back to the pool on release.

- Guest Agent (HTTP service)
  - Executes UI actions using `pynput`.
  - Manages Simulator lifecycle and screenshots.
  - Binds to `0.0.0.0` for VM reachability.

## Files

`__init__.py`
Exports the public API for the package (config loader, pool, episode handle).

`config.py`
Loads JSON/YAML instance configs into dataclasses. Defines `InstanceConfig` and
`PoolConfig`.

`host_controller.py`
HTTP server that manages the pool of guest VMs on a host instance. Supports
`/acquire_vm` and `/release_vm` for leasing.

`guest_agent.py`
HTTP server that runs inside each guest VM. Injects UI actions via `pynput`,
controls the Simulator lifecycle, and captures screenshots.

`episode.py`
Async client for a single rollout. Sends HTTP actions directly to the guest
agent, tracks `max_steps`, and exposes `tap`, `swipe`, `observe`, etc.

`pool.py`
Async pool across instances. Uses a semaphore for concurrency and round-robin
instance selection to acquire VMs.

`README.md`
Usage examples for running the host controller, guest agent, and async pool.
