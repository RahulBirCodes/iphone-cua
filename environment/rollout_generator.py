"""Rollout generator abstraction with a Gymnasium-style API for LLM agents."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


class RolloutGenerator:
    """Generates rollouts with a step-based API for LLM-driven agents.

    Expected inputs:
        - env_config: Dict describing environment settings (timeouts, limits).
        - action_space: Description of actions the agent can emit.
        - observation_space: Description of observations the generator returns.

    Expected outputs:
        - reset() returns initial observation and info dict.
        - step() returns (observation, reward, terminated, truncated, info).
    """

    def __init__(
        self,
        env_config: Optional[Dict[str, Any]] = None,
        action_space: Optional[Any] = None,
        observation_space: Optional[Any] = None,
    ) -> None:
        self.env_config = env_config or {}
        self.action_space = action_space
        self.observation_space = observation_space

    def reset(self, seed: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Start a new rollout and return the initial observation.

        Expected inputs:
            - seed: Optional random seed for reproducibility.

        Expected outputs:
            - observation: Dict describing the initial state.
            - info: Dict with metadata (episode_id, timing, etc.).
        """
        raise NotImplementedError

    def step(
        self, action: Any
    ) -> Tuple[Dict[str, Any], float, bool, bool, Dict[str, Any]]:
        """Apply an action and advance the rollout.

        Expected inputs:
            - action: One action from the declared action_space.

        Expected outputs:
            - observation: Dict describing the new state.
            - reward: Float reward (or proxy score).
            - terminated: True if episode ended by terminal condition.
            - truncated: True if episode ended by time/step limit.
            - info: Dict with metadata (latency, errors, etc.).
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the generator.

        Expected inputs:
            - None.

        Expected outputs:
            - None.
        """
        raise NotImplementedError

