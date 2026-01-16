# Environment Architecture (new_arch)

This document summarizes the architecture described in `new_arch/` for the
IPhoneCua environment.

## Overview

The system is split into three layers to keep per-step latency low and to
maximize parallel rollouts:

1) Host instance (host machine)
   - Runs a Host Controller that tracks a pool of guest VMs.
   - Manages VM leasing and release only (no per-step routing).

2) Guest VM (macOS virtual machine)
   - Runs an iOS Simulator and a long-running Guest Agent HTTP server.
   - Injects UI actions via `pynput` and captures screenshots.
   - Resets per episode with `xcrun simctl erase all` (no VM snapshots).

3) Python RL environment layer
   - Uses an async pool to lease VMs and run rollouts.
   - Sends actions directly to Guest Agents (lowest-latency path).
   - One rollout maps to one VM lease for parallelism.

## Core components and responsibilities

### Host Controller (HTTP service)

- Lives on the host instance.
- Tracks free vs. leased VMs.
- Endpoints:
  - `POST /acquire_vm` returns `{ vm_id, vm_ip, agent_port }`.
  - `POST /release_vm` releases a VM back to the pool.

### Guest Agent (HTTP service)

- Runs inside each guest VM.
- Executes UI actions (tap, swipe, etc.) using `pynput`.
- Manages simulator lifecycle and screenshots.
- Binds to `0.0.0.0` so the host can reach it inside the VM.

### IPhoneCuaPool

- Global async pool across all configured host instances.
- Uses a semaphore to limit total concurrency.
- Uses round-robin instance selection for VM leases.
- `acquire()` returns an `EpisodeHandle`.

### EpisodeHandle

- Represents a single rollout bound to one guest VM.
- Sends actions directly to the guest agent.
- Enforces `max_steps` and provides methods like:
  - `tap`, `swipe`, `observe`, `reset`, `finished`, `close`.

## Data flow (single rollout)

1) Client calls `IPhoneCuaPool.acquire()`.
2) Pool requests a VM from the Host Controller.
3) Pool returns an `EpisodeHandle` for that VM.
4) `EpisodeHandle` sends actions directly to the Guest Agent.
5) On completion, client calls `release()` to return the VM.

## Configuration

- Instances are defined in JSON or YAML with fields like:
  - `id`
  - `host_controller_url`
  - `max_concurrent`

