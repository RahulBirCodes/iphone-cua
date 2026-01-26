# Rollout Environment Architecture

## Intuitive Overview

Imagine you want to train an LLM to use an iPhone. The LLM sees a screenshot, decides what to do (tap, swipe, type), and we need to tell it if it did well. We need to run hundreds of these interactions in parallel to train efficiently.

### The Core Loop (Single Rollout)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        One Episode                                  │
│                                                                     │
│   Task: "Open Settings and turn on Dark Mode"                       │
│                                                                     │
│   ┌──────────┐      ┌──────────┐      ┌──────────┐                  │
│   │Screenshot│ ───► │   LLM    │ ───► │  Action  │                  │
│   │  + Task  │      │  Agent   │      │ (as XML) │                  │
│   └──────────┘      └──────────┘      └──────────┘                  │
│        ▲                                   │                        │
│        │                                   ▼                        │
│        │            ┌──────────┐      ┌──────────┐                  │
│        │            │  Reward  │ ◄─── │  Parse   │                  │
│        │            │ + Done?  │      │& Execute │                  │
│        │            └──────────┘      └──────────┘                  │
│        │                 │                 │                        │
│        └─────────────────┴─────────────────┘                        │
│                                                                     │
│   Repeat until: task complete OR max_steps reached                  │
└─────────────────────────────────────────────────────────────────────┘
```

**What happens each step:**
1. LLM sees the current screenshot + task description
2. LLM outputs an action in XML format: `<action type="tap"><x>0.5</x><y>0.3</y></action>`
3. We parse the XML and execute the action on the iOS simulator
4. We compute a reward (small penalty per step, big reward if task done)
5. Get new screenshot, repeat

### The Scale Problem

You want 256 parallel rollouts. But you don't have 256 iPhones. Instead:

- You have **N EC2 instances** (hosts)
- Each host runs **M Tart VMs** (virtual Macs with iOS simulators)
- Total capacity: N × M simulators

The system needs to:
1. Distribute 256 rollouts across available VMs
2. Handle the network complexity (VMs are inside EC2 instances)
3. Make this invisible to your training code

### The Solution: Layered Abstraction

```
Training Code (you write this)
        │
        │  "Give me 256 environments"
        ▼
┌─────────────────────────────────────┐
│         IPhoneCuaEnv                │  ◄── Gym-like interface
│   env.reset(), env.step(action)    │      You interact with this
└─────────────────────────────────────┘
        │
        │  "Get me a VM from somewhere"
        ▼
┌─────────────────────────────────────┐
│         LoadBalancer                │  ◄── Picks best host
│   Tracks VM availability per host   │      Handles failures
└─────────────────────────────────────┘
        │
        │  "Acquire VM from this host"
        ▼
┌─────────────────────────────────────┐
│         HostClient                  │  ◄── Talks to one EC2
│   SSH tunnel + HTTP to host         │      Manages connection
└─────────────────────────────────────┘
        │
        │  SSH tunnel to VM
        ▼
┌─────────────────────────────────────┐
│      VM (inside EC2)                │
│   ┌─────────────────────────────┐   │
│   │     vm_controller           │   │  ◄── HTTP server in VM
│   │   tap/swipe/screenshot      │   │      Executes actions
│   └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

---

## Detailed Architecture

### 1. Training Loop Integration

Here's how your training code uses this system:

```python
import asyncio
from environment import IPhoneCuaEnv, RolloutBatch

async def generate_rollouts(
    policy: LLMPolicy,
    tasks: List[str],
    group_size: int = 256
) -> List[Trajectory]:
    """Generate a batch of rollouts in parallel."""

    # Create 256 environments - automatically distributed across hosts
    envs = await asyncio.gather(*[
        IPhoneCuaEnv.create(task=task)
        for task in tasks[:group_size]
    ])

    trajectories = []

    try:
        # Run all rollouts in parallel
        trajectories = await asyncio.gather(*[
            run_single_rollout(env, policy)
            for env in envs
        ])
    finally:
        # Release all VMs back to pool
        await asyncio.gather(*[env.close() for env in envs])

    return trajectories


async def run_single_rollout(
    env: IPhoneCuaEnv,
    policy: LLMPolicy
) -> Trajectory:
    """Run one episode, collecting (state, action, reward) tuples."""

    trajectory = Trajectory()
    obs = await env.reset()
    done = False

    while not done:
        # LLM generates action as XML string
        llm_output = await policy.get_action(
            screenshot=obs["screenshot_b64"],
            task=obs["task"],
            history=trajectory.history
        )

        # Environment parses XML, executes, computes reward
        next_obs, reward, done, info = await env.step(llm_output)

        trajectory.add(obs, llm_output, reward, info)
        obs = next_obs

    return trajectory


# Main training loop
async def train():
    policy = LLMPolicy(model="your-model")
    tasks = load_task_dataset()

    for epoch in range(num_epochs):
        # Generate 256 rollouts in parallel
        trajectories = await generate_rollouts(
            policy=policy,
            tasks=sample_tasks(tasks, n=256),
            group_size=256
        )

        # Compute advantages, update policy (GRPO, PPO, etc.)
        policy.update(trajectories)
```

### 2. IPhoneCuaEnv (The User-Facing Interface)

This is the only class your training code interacts with directly.

```python
class IPhoneCuaEnv:
    """Async Gym-like environment for iOS simulator control.

    Abstracts away:
    - VM pool management
    - Host selection / load balancing
    - SSH tunneling
    - Network complexity

    You just call step() and reset().
    """

    @classmethod
    async def create(
        cls,
        task: str,
        config_path: str = "config.yaml",
        max_steps: int = 200,
        reward_config: Optional[RewardConfig] = None,
    ) -> "IPhoneCuaEnv":
        """Factory method - acquires a VM and returns ready-to-use env.

        This method:
        1. Gets the global LoadBalancer instance
        2. Acquires a VM from the best available host
        3. Sets up SSH tunnel to the VM
        4. Returns an env ready for reset()

        Args:
            task: The task description for this episode
            config_path: Path to hosts configuration
            max_steps: Maximum steps before forced termination
            reward_config: Custom reward values (optional)
        """
        config = load_config(config_path)
        lb = await LoadBalancer.get_instance(config)
        host, vm_info, tunnel = await lb.acquire_vm()

        return cls(
            task=task,
            host=host,
            vm_info=vm_info,
            tunnel=tunnel,
            lb=lb,
            max_steps=max_steps,
            reward_config=reward_config or RewardConfig(),
        )

    async def reset(self) -> Observation:
        """Reset the simulator and return initial observation.

        Calls the vm_controller's reset endpoint which:
        1. Erases simulator state (xcrun simctl erase)
        2. Boots simulator fresh
        3. Takes initial screenshot

        Returns:
            obs: {
                "screenshot_b64": str,  # Base64 PNG
                "task": str,            # Task description
                "step": 0,
            }
        """
        self._step_count = 0
        self._history = []

        await self._episode.reset()
        screenshot = await self._episode.observe()

        return {
            "screenshot_b64": screenshot["screenshot_b64"],
            "task": self._task,
            "step": 0,
        }

    async def step(
        self,
        llm_output: str
    ) -> Tuple[Observation, float, bool, Dict[str, Any]]:
        """Execute an action and return (obs, reward, done, info).

        Args:
            llm_output: Raw string from LLM containing XML action
                       e.g., '<action type="tap"><x>0.5</x><y>0.3</y></action>'

        Returns:
            obs: New observation after action
            reward: Scalar reward for this step
            done: True if episode is over
            info: {
                "step": int,
                "error": Optional[str],  # If action was invalid
                "task_complete": bool,   # If task was completed
                "judge_reasoning": str,  # If judge was called
            }
        """
        self._step_count += 1
        reward = self._reward_config.step_penalty  # Start with step cost
        info = {"step": self._step_count}

        # 1. Parse action from LLM output
        action, parse_error = parse_action(llm_output)

        if parse_error:
            # Invalid XML or action format
            info["error"] = parse_error
            reward += self._reward_config.invalid_action_penalty
            obs = await self._get_observation()
            done = self._step_count >= self._max_steps
            if done:
                reward += self._reward_config.max_steps_penalty
            return obs, reward, done, info

        # 2. Handle "done" action (agent claims task complete)
        if action["type"] == "done":
            obs = await self._get_observation()
            is_complete, reasoning = await self._judge.is_complete(
                task=self._task,
                screenshot_b64=obs["screenshot_b64"],
                history=self._history,
            )
            info["judge_reasoning"] = reasoning

            if is_complete:
                info["task_complete"] = True
                reward += self._reward_config.task_complete_reward
                return obs, reward, True, info
            else:
                # False claim of completion
                info["error"] = "Task not complete"
                info["task_complete"] = False
                reward += self._reward_config.invalid_action_penalty
                done = self._step_count >= self._max_steps
                if done:
                    reward += self._reward_config.max_steps_penalty
                return obs, reward, done, info

        # 3. Execute normal action
        try:
            await self._execute_action(action)
        except ActionError as e:
            info["error"] = str(e)
            reward += self._reward_config.invalid_action_penalty

        # 4. Get new observation
        obs = await self._get_observation()
        self._history.append((llm_output, obs["screenshot_b64"]))

        # 5. Check termination
        done = self._step_count >= self._max_steps
        if done:
            reward += self._reward_config.max_steps_penalty

        return obs, reward, done, info

    async def close(self) -> None:
        """Release the VM back to the pool.

        MUST be called when done with the environment.
        Use try/finally to ensure cleanup.
        """
        if self._closed:
            return
        self._closed = True

        # Close SSH tunnel
        if self._tunnel:
            await self._tunnel.close()

        # Release VM back to host
        await self._lb.release_vm(self._host, self._vm_info.vm_id)

    async def _execute_action(self, action: Dict[str, Any]) -> None:
        """Execute a parsed action on the simulator."""
        action_type = action["type"]

        if action_type == "tap":
            await self._episode.tap(action["x"], action["y"])
        elif action_type == "swipe":
            await self._episode.swipe(
                action["x1"], action["y1"],
                action["x2"], action["y2"]
            )
        elif action_type == "type":
            await self._episode.type_text(action["text"])
        elif action_type == "home":
            await self._episode.go_home()
        elif action_type == "wait":
            await self._episode.wait(action.get("seconds", 0.5))
        else:
            raise ActionError(f"Unknown action type: {action_type}")

    async def _get_observation(self) -> Observation:
        """Get current observation from simulator."""
        result = await self._episode.observe()
        return {
            "screenshot_b64": result["screenshot_b64"],
            "task": self._task,
            "step": self._step_count,
        }
```

### 3. Action Format (XML)

LLM outputs actions in XML for structured, reliable parsing:

```xml
<!-- Tap at normalized coordinates (0-1) -->
<action type="tap">
  <x>0.5</x>
  <y>0.3</y>
</action>

<!-- Swipe from point A to point B -->
<action type="swipe">
  <x1>0.2</x1>
  <y1>0.5</y1>
  <x2>0.8</x2>
  <y2>0.5</y2>
</action>

<!-- Type text (simulator keyboard) -->
<action type="type">
  <text>hello world</text>
</action>

<!-- Press home button -->
<action type="home"/>

<!-- Wait for animations/loading -->
<action type="wait">
  <seconds>1.5</seconds>
</action>

<!-- Agent declares task complete (triggers judge) -->
<action type="done"/>
```

**Why XML?**
- Structured and unambiguous
- Easy to parse with standard libraries
- LLMs are good at generating valid XML
- Clear error messages when malformed
- Can be embedded in longer LLM responses (we extract the `<action>` tag)

**Action Parser:**

```python
import re
import xml.etree.ElementTree as ET
from typing import Tuple, Optional, Dict, Any

def parse_action(llm_output: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse an action from LLM output.

    Args:
        llm_output: Raw string from LLM, should contain <action>...</action>

    Returns:
        (action_dict, error_message)
        On success: ({"type": "tap", "x": 0.5, "y": 0.3}, None)
        On failure: (None, "Error description")
    """
    # Extract <action>...</action> from response
    match = re.search(r'<action[^>]*>.*?</action>|<action[^/]*/>', llm_output, re.DOTALL)
    if not match:
        return None, "No <action> tag found in response"

    xml_str = match.group(0)

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        return None, f"Invalid XML: {e}"

    action_type = root.get("type")
    if not action_type:
        return None, "Missing 'type' attribute on <action>"

    try:
        if action_type == "tap":
            return {
                "type": "tap",
                "x": float(root.find("x").text),
                "y": float(root.find("y").text),
            }, None

        elif action_type == "swipe":
            return {
                "type": "swipe",
                "x1": float(root.find("x1").text),
                "y1": float(root.find("y1").text),
                "x2": float(root.find("x2").text),
                "y2": float(root.find("y2").text),
            }, None

        elif action_type == "type":
            text_elem = root.find("text")
            if text_elem is None or text_elem.text is None:
                return None, "Missing <text> element for type action"
            return {"type": "type", "text": text_elem.text}, None

        elif action_type == "home":
            return {"type": "home"}, None

        elif action_type == "wait":
            seconds_elem = root.find("seconds")
            seconds = float(seconds_elem.text) if seconds_elem is not None else 0.5
            return {"type": "wait", "seconds": seconds}, None

        elif action_type == "done":
            return {"type": "done"}, None

        else:
            return None, f"Unknown action type: {action_type}"

    except (AttributeError, ValueError, TypeError) as e:
        return None, f"Invalid action parameters: {e}"
```

### 4. Reward Function

```python
from dataclasses import dataclass

@dataclass
class RewardConfig:
    """Configuration for reward computation.

    Default values tuned for typical RL training:
    - Small step penalty encourages efficiency
    - Larger penalty for invalid actions discourages malformed outputs
    - Large positive reward for task completion
    """

    # Per-step cost (encourages finishing quickly)
    step_penalty: float = -0.01

    # Penalty for invalid actions (malformed XML, out of bounds, etc.)
    invalid_action_penalty: float = -0.1

    # Penalty for hitting max_steps without completing task
    max_steps_penalty: float = -0.5

    # Reward for successfully completing the task
    task_complete_reward: float = 1.0


# Example reward scenarios for a 50-step episode:
#
# Scenario A: Complete task in 20 steps
#   reward = (20 × -0.01) + 1.0 = 0.80
#
# Scenario B: Complete task in 50 steps
#   reward = (50 × -0.01) + 1.0 = 0.50
#
# Scenario C: Hit max_steps (100) without completion
#   reward = (100 × -0.01) + (-0.5) = -1.50
#
# Scenario D: 30 steps with 5 invalid actions, then complete
#   reward = (30 × -0.01) + (5 × -0.1) + 1.0 = 0.20
```

### 5. LLM-as-Judge for Task Completion

When the agent outputs `<action type="done"/>`, we verify with an LLM judge:

```python
from openai import AsyncOpenAI
from typing import Tuple, List
import hashlib
import asyncio

class TaskJudge:
    """Uses an LLM to determine if a task has been completed.

    Design considerations:
    - Uses a fast, cheap model (gpt-4o-mini) for quick evaluation
    - Caches results to avoid redundant API calls
    - Async to not block the main loop
    - Includes retry logic for API failures
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        cache_size: int = 1000,
        max_retries: int = 3,
    ):
        self._client = AsyncOpenAI()
        self._model = model
        self._cache: Dict[str, Tuple[bool, str]] = {}
        self._cache_size = cache_size
        self._max_retries = max_retries

    async def is_complete(
        self,
        task: str,
        screenshot_b64: str,
        history: List[Tuple[str, str]],  # [(action_xml, screenshot_b64), ...]
    ) -> Tuple[bool, str]:
        """Evaluate if the task has been completed.

        Args:
            task: The task description
            screenshot_b64: Current screenshot (base64 PNG)
            history: List of (action, screenshot) pairs from the episode

        Returns:
            (is_complete, reasoning)
        """
        # Check cache
        cache_key = self._make_cache_key(task, screenshot_b64)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Build prompt
        prompt = self._build_prompt(task)

        # Call LLM with retries
        for attempt in range(self._max_retries):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{screenshot_b64}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=200,
                )

                result = self._parse_response(response.choices[0].message.content)

                # Cache result
                if len(self._cache) >= self._cache_size:
                    # Simple cache eviction: remove oldest entry
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = result

                return result

            except Exception as e:
                if attempt == self._max_retries - 1:
                    # Final attempt failed, assume not complete
                    return False, f"Judge error: {e}"
                await asyncio.sleep(0.5 * (attempt + 1))  # Backoff

        return False, "Judge failed after retries"

    def _build_prompt(self, task: str) -> str:
        return f"""You are evaluating if a task has been completed on an iOS device.

Task: {task}

Look at the screenshot and determine if the task has been successfully completed.

Respond in this exact format:
<evaluation>
  <complete>true</complete>  <!-- or false -->
  <reasoning>Brief explanation of why the task is or is not complete</reasoning>
</evaluation>

Be strict: the task must be fully complete, not partially done."""

    def _parse_response(self, response: str) -> Tuple[bool, str]:
        """Parse the judge's XML response."""
        try:
            import xml.etree.ElementTree as ET

            # Extract <evaluation> tag
            match = re.search(r'<evaluation>.*?</evaluation>', response, re.DOTALL)
            if not match:
                return False, "Could not parse judge response"

            root = ET.fromstring(match.group(0))
            complete_text = root.find("complete").text.strip().lower()
            reasoning = root.find("reasoning").text.strip()

            is_complete = complete_text == "true"
            return is_complete, reasoning

        except Exception as e:
            return False, f"Parse error: {e}"

    def _make_cache_key(self, task: str, screenshot_b64: str) -> str:
        """Create a cache key from task and screenshot."""
        content = f"{task}:{screenshot_b64[:100]}"  # Use first 100 chars of screenshot
        return hashlib.md5(content.encode()).hexdigest()
```

### 6. LoadBalancer

The LoadBalancer distributes rollouts across available hosts:

```python
import asyncio
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass

@dataclass
class HostInfo:
    """Configuration for one EC2 host."""
    host_id: str
    host_controller_url: str  # URL to reach the host controller
    ssh_host: str             # SSH hostname/IP
    ssh_user: str             # SSH username
    ssh_key_path: str         # Path to SSH private key
    max_concurrent: int       # Max VMs on this host


class LoadBalancer:
    """Distributes VM acquisition across multiple hosts.

    Selection strategy:
    1. Query all hosts for available VM count
    2. Sort by availability (most free VMs first)
    3. Try to acquire from each in order until success
    4. Track failed hosts and temporarily deprioritize them

    Thread-safe: uses asyncio.Lock for concurrent access.
    Singleton: one instance shared across all environments.
    """

    _instance: Optional["LoadBalancer"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, config: PoolConfig) -> "LoadBalancer":
        """Get or create the singleton LoadBalancer."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls(config)
            return cls._instance

    def __init__(self, config: PoolConfig):
        self._hosts: List[HostClient] = [
            HostClient(host) for host in config.hosts
        ]
        self._acquire_lock = asyncio.Lock()
        self._failed_hosts: Dict[str, float] = {}  # host_id -> failure_time
        self._failure_cooldown = 60.0  # seconds to wait before retrying failed host

    async def acquire_vm(self) -> Tuple[HostClient, VMInfo, SSHTunnel]:
        """Acquire a VM from the best available host.

        Returns:
            (host_client, vm_info, ssh_tunnel)

        Raises:
            RuntimeError: If no VMs available on any host
        """
        async with self._acquire_lock:
            # Get availability from all hosts (in parallel)
            availabilities = await asyncio.gather(*[
                self._get_host_availability(host)
                for host in self._hosts
            ], return_exceptions=True)

            # Build list of (host, free_count) sorted by availability
            host_availability = []
            for host, result in zip(self._hosts, availabilities):
                if isinstance(result, Exception):
                    self._mark_failed(host.host_id)
                    continue
                if self._is_failed(host.host_id):
                    continue
                host_availability.append((host, result))

            # Sort by free VM count (descending)
            host_availability.sort(key=lambda x: x[1], reverse=True)

            # Try each host in order
            last_error = None
            for host, free_count in host_availability:
                if free_count == 0:
                    continue

                try:
                    vm_info, tunnel = await host.acquire_vm()
                    return host, vm_info, tunnel
                except Exception as e:
                    last_error = e
                    self._mark_failed(host.host_id)
                    continue

            if last_error:
                raise RuntimeError(f"Failed to acquire VM: {last_error}")
            raise RuntimeError("No VMs available on any host")

    async def release_vm(
        self,
        host: HostClient,
        vm_id: str
    ) -> None:
        """Release a VM back to its host."""
        try:
            await host.release_vm(vm_id)
        except Exception:
            # Log but don't raise - VM will eventually timeout on host
            pass

    async def _get_host_availability(self, host: HostClient) -> int:
        """Query a host for available VM count."""
        return await host.get_free_count()

    def _mark_failed(self, host_id: str) -> None:
        """Mark a host as temporarily failed."""
        import time
        self._failed_hosts[host_id] = time.time()

    def _is_failed(self, host_id: str) -> bool:
        """Check if a host is in failure cooldown."""
        import time
        if host_id not in self._failed_hosts:
            return False
        if time.time() - self._failed_hosts[host_id] > self._failure_cooldown:
            del self._failed_hosts[host_id]
            return False
        return True
```

### 7. HostClient and SSH Tunneling

```python
import asyncio
import subprocess
from dataclasses import dataclass
from typing import Tuple, Optional

@dataclass
class SSHTunnel:
    """Manages an SSH tunnel to a VM."""

    process: asyncio.subprocess.Process
    local_port: int

    @classmethod
    async def create(
        cls,
        ssh_host: str,
        ssh_user: str,
        ssh_key_path: str,
        local_port: int,
        remote_host: str,  # VM's internal IP
        remote_port: int,  # VM's agent port
    ) -> "SSHTunnel":
        """Create an SSH tunnel.

        Creates: localhost:local_port -> remote_host:remote_port
        Through: ssh_user@ssh_host
        """
        cmd = [
            "ssh",
            "-N",  # Don't execute remote command
            "-L", f"{local_port}:{remote_host}:{remote_port}",
            "-i", ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            f"{ssh_user}@{ssh_host}",
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait a bit for tunnel to establish
        await asyncio.sleep(0.5)

        if process.returncode is not None:
            stderr = await process.stderr.read()
            raise RuntimeError(f"SSH tunnel failed: {stderr.decode()}")

        return cls(process=process, local_port=local_port)

    async def close(self) -> None:
        """Close the SSH tunnel."""
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()


class HostClient:
    """Client for communicating with one EC2 host."""

    _port_counter: int = 19000  # Starting port for tunnels
    _port_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, host_info: HostInfo):
        self._info = host_info
        self._http_client = HTTPClient(host_info.host_controller_url)

    @property
    def host_id(self) -> str:
        return self._info.host_id

    async def get_free_count(self) -> int:
        """Get number of free VMs on this host."""
        response = await self._http_client.get("/status")
        return response.get("free_count", 0)

    async def acquire_vm(self) -> Tuple[VMInfo, SSHTunnel]:
        """Acquire a VM and create SSH tunnel to it.

        Returns:
            (vm_info, tunnel) where vm_info has tunneled connection details
        """
        # 1. Request VM from host controller
        response = await self._http_client.post("/acquire_vm")

        vm_info = VMInfo(
            vm_id=response["vm_id"],
            vm_ip=response["vm_ip"],      # Internal IP (192.168.64.x)
            agent_port=response["agent_port"],
        )

        # 2. Allocate local port for tunnel
        local_port = await self._allocate_port()

        # 3. Create SSH tunnel
        tunnel = await SSHTunnel.create(
            ssh_host=self._info.ssh_host,
            ssh_user=self._info.ssh_user,
            ssh_key_path=self._info.ssh_key_path,
            local_port=local_port,
            remote_host=vm_info.vm_ip,
            remote_port=vm_info.agent_port,
        )

        # 4. Return VM info with tunneled endpoint
        tunneled_vm = VMInfo(
            vm_id=vm_info.vm_id,
            vm_ip="127.0.0.1",       # Tunnel endpoint
            agent_port=local_port,    # Tunnel port
        )

        return tunneled_vm, tunnel

    async def release_vm(self, vm_id: str) -> None:
        """Release a VM back to the pool."""
        await self._http_client.post("/release_vm", {"vm_id": vm_id})

    @classmethod
    async def _allocate_port(cls) -> int:
        """Allocate a unique local port for SSH tunnel."""
        async with cls._port_lock:
            port = cls._port_counter
            cls._port_counter += 1
            if cls._port_counter > 29000:
                cls._port_counter = 19000
            return port
```

### 8. Scaling to 256 Parallel Rollouts

Here's how the system handles 256 concurrent rollouts:

```
Configuration example:
- 4 EC2 instances (hosts)
- Each host runs 8 Tart VMs
- Total capacity: 32 VMs

For 256 rollouts with 32 VMs:
- Each VM handles ~8 sequential episodes
- All 32 VMs run in parallel
- Total throughput: 32 parallel × 8 sequential = 256 rollouts
```

**Batched Rollout Generation:**

```python
async def generate_rollouts_batched(
    policy: LLMPolicy,
    tasks: List[str],
    total_rollouts: int = 256,
    max_parallel: int = 32,  # Limited by VM count
) -> List[Trajectory]:
    """Generate rollouts in batches when we have fewer VMs than rollouts.

    With 32 VMs and 256 rollouts needed:
    - Run 8 batches of 32 parallel rollouts
    """
    all_trajectories = []

    for batch_start in range(0, total_rollouts, max_parallel):
        batch_end = min(batch_start + max_parallel, total_rollouts)
        batch_tasks = tasks[batch_start:batch_end]

        # Create environments for this batch
        envs = await asyncio.gather(*[
            IPhoneCuaEnv.create(task=task)
            for task in batch_tasks
        ])

        try:
            # Run batch in parallel
            batch_trajectories = await asyncio.gather(*[
                run_single_rollout(env, policy)
                for env in envs
            ])
            all_trajectories.extend(batch_trajectories)
        finally:
            # Release all VMs
            await asyncio.gather(*[env.close() for env in envs])

    return all_trajectories
```

**Environment Pool (Reuse VMs Across Episodes):**

For maximum efficiency, reuse VMs across episodes instead of releasing after each:

```python
class EnvPool:
    """Pool of reusable environments for efficient batch rollouts.

    Instead of create/close for each episode, keep VMs leased
    and just reset() between episodes.
    """

    def __init__(self, size: int, config_path: str = "config.yaml"):
        self._size = size
        self._config_path = config_path
        self._envs: List[IPhoneCuaEnv] = []
        self._available: asyncio.Queue[IPhoneCuaEnv] = asyncio.Queue()

    async def initialize(self) -> None:
        """Create all environments upfront."""
        self._envs = await asyncio.gather(*[
            IPhoneCuaEnv.create(task="", config_path=self._config_path)
            for _ in range(self._size)
        ])
        for env in self._envs:
            await self._available.put(env)

    async def acquire(self, task: str) -> IPhoneCuaEnv:
        """Get an environment from the pool."""
        env = await self._available.get()
        env._task = task  # Set new task
        return env

    async def release(self, env: IPhoneCuaEnv) -> None:
        """Return environment to pool (don't close, just reset)."""
        await self._available.put(env)

    async def close_all(self) -> None:
        """Close all environments (call at end of training)."""
        await asyncio.gather(*[env.close() for env in self._envs])


# Usage in training:
async def train_with_pool():
    pool = EnvPool(size=32)
    await pool.initialize()

    try:
        for epoch in range(num_epochs):
            tasks = sample_tasks(n=256)
            trajectories = []

            # Process in batches of 32
            for i in range(0, 256, 32):
                batch_tasks = tasks[i:i+32]

                # Acquire 32 envs from pool
                envs = await asyncio.gather(*[
                    pool.acquire(task) for task in batch_tasks
                ])

                # Run rollouts
                batch_trajs = await asyncio.gather(*[
                    run_single_rollout(env, policy)
                    for env in envs
                ])
                trajectories.extend(batch_trajs)

                # Return envs to pool (fast, no VM release)
                await asyncio.gather(*[
                    pool.release(env) for env in envs
                ])

            # Update policy
            policy.update(trajectories)

    finally:
        await pool.close_all()
```

### 9. Configuration

```yaml
# config.yaml

hosts:
  - host_id: "ec2-1"
    host_controller_url: "http://10.0.1.10:7000"
    ssh_host: "10.0.1.10"
    ssh_user: "admin"
    ssh_key_path: "~/.ssh/ec2-key.pem"
    max_concurrent: 8

  - host_id: "ec2-2"
    host_controller_url: "http://10.0.1.11:7000"
    ssh_host: "10.0.1.11"
    ssh_user: "admin"
    ssh_key_path: "~/.ssh/ec2-key.pem"
    max_concurrent: 8

  - host_id: "ec2-3"
    host_controller_url: "http://10.0.1.12:7000"
    ssh_host: "10.0.1.12"
    ssh_user: "admin"
    ssh_key_path: "~/.ssh/ec2-key.pem"
    max_concurrent: 8

  - host_id: "ec2-4"
    host_controller_url: "http://10.0.1.13:7000"
    ssh_host: "10.0.1.13"
    ssh_user: "admin"
    ssh_key_path: "~/.ssh/ec2-key.pem"
    max_concurrent: 8

env:
  max_steps: 200

reward:
  step_penalty: -0.01
  invalid_action_penalty: -0.1
  max_steps_penalty: -0.5
  task_complete_reward: 1.0

judge:
  model: "gpt-4o-mini"
  cache_size: 1000
```

---

## File Structure

```
environment/
├── __init__.py
├── env.py                 # IPhoneCuaEnv (main interface)
├── action_parser.py       # XML action parsing
├── reward.py              # RewardConfig
├── task_judge.py          # LLM-as-Judge
├── load_balancer.py       # LoadBalancer
├── host_client.py         # HostClient + SSHTunnel
├── config.py              # Config loading
├── host_controller.py     # HostController (runs on EC2)
├── vm_controller.py       # VMController (runs in VM)
├── env_pool.py            # EnvPool for VM reuse
└── tests/
    ├── test_env.py
    ├── test_action_parser.py
    ├── test_load_balancer.py
    └── test_host_controller.py
```

---

## Summary

| Component | Responsibility |
|-----------|----------------|
| `IPhoneCuaEnv` | Gym-like interface (step, reset, close) |
| `ActionParser` | Parse XML actions from LLM output |
| `RewardConfig` | Compute rewards (step penalty, completion bonus) |
| `TaskJudge` | LLM evaluation of task completion |
| `LoadBalancer` | Distribute rollouts across hosts |
| `HostClient` | Communicate with one EC2 host |
| `SSHTunnel` | Tunnel to reach VMs inside EC2 |
| `HostController` | Manage VM pool on one host |
| `VMController` | Execute actions on iOS simulator |
| `EnvPool` | Reuse VMs across episodes for efficiency |
