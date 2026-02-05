"""Integration test: spawn one iPhoneEnv + one MLXActor and generate one rollout.

Requires:
- A cached Qwen3-VL model (or set MLX_TEST_ALLOW_DOWNLOAD=1)
- A tart VM base image (set BASE_IMAGE env var, default "iphone-base")
- An iphone_slot resource available

Run with:
    pytest tests/test_integration_rollout.py -s
"""

import os

os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import ray
import pytest
from huggingface_hub import snapshot_download
from huggingface_hub.utils import LocalEntryNotFoundError

from environment.inference.mlx_inference import MLXActor, MLX_VLM_MODEL_MAP
from environment.iphone_env import iPhoneEnv
from environment.parsers import parse as action_parse
from environment.parsers.qwen3_response_parser import qwen3_response_parser
from environment.schemas import RewardPolicy, RolloutResult, TerminationReason


def _always_false_judge(turns: list, task_id: str, task_prompt: str) -> bool:
    _ = turns
    _ = task_id
    _ = task_prompt
    return False


def _ensure_model_cached(model_id: str) -> None:
    try:
        snapshot_download(model_id, local_files_only=True)
    except LocalEntryNotFoundError:
        pytest.skip(
            f"Model '{model_id}' not cached. Set MLX_TEST_ALLOW_DOWNLOAD=1 "
            "to allow downloads."
        )


def test_single_rollout() -> None:
    """Spawn one iPhoneEnv and one MLXActor, run a single rollout end-to-end."""
    base_image = os.environ.get("BASE_IMAGE", "iphone-base")
    model_id = os.environ.get("MLX_TEST_MODEL", "Qwen/Qwen3-VL-2B-Thinking")
    allow_download = os.environ.get("MLX_TEST_ALLOW_DOWNLOAD", "0") == "1"

    resolved_model_id = MLX_VLM_MODEL_MAP.get(model_id, model_id)
    if not allow_download:
        _ensure_model_cached(resolved_model_id)

    ray.init(
        address="local",
        num_cpus=2,
        num_gpus=0,
        resources={"inference_node": 1, "iphone_slot": 1},
    )
    try:
        # Spawn inference actor
        inference_actor = MLXActor.options(num_gpus=0).remote(
            model=model_id,
            parse_model_output=qwen3_response_parser,
            max_kv_size=4096,
            trust_remote_code=True,
        )

        sampling = {"temperature": 0.6, "max_tokens": 512}

        # Spawn iPhoneEnv wired to the inference actor
        env = iPhoneEnv.remote(
            base_image=base_image,
            inference_actor=inference_actor,
            sampling=sampling,
            parse_fn=action_parse,
            judge_fn=_always_false_judge,
            reward_policy=RewardPolicy(),
        )

        # Run one rollout
        result: RolloutResult = ray.get(
            env.collect_rollout.remote(
                system_prompt="You are an AI agent controlling an iPhone. Respond with exactly one XML action tag.",
                task_id="integration-test-001",
                task_prompt="Open the Settings app.",
                max_turns=3,
                save_json=True,
            )
        )

        # Basic structural checks
        assert isinstance(result, RolloutResult)
        assert result.task_id == "integration-test-001"
        assert result.termination_reason in (
            TerminationReason.DONE,
            TerminationReason.FAIL,
            TerminationReason.TRUNCATED,
        )
        assert len(result.turns) >= 3  # system + user + at least one assistant
        assert result.turns[0].role == "system"
        assert result.turns[1].role == "user"
        assert result.turns[1].content == "Open the Settings app."
        assert result.turns[1].screenshot is None

        # Check assistant turns have the new schema fields
        assistant_turns = [t for t in result.turns if t.role == "assistant"]
        assert len(assistant_turns) >= 1
        for t in assistant_turns:
            # content and reasoning should be str | None (new schema)
            assert not hasattr(t, "raw_output"), "Turn should not have raw_output"
            assert not hasattr(t, "parsed_output"), "Turn should not have parsed_output"
            assert hasattr(t, "reasoning")
            assert hasattr(t, "content")

        print(f"\n\nROLLOUT COMPLETE: {len(result.turns)} turns, "
              f"termination={result.termination_reason.value}, "
              f"reward={result.final_reward}\n")

    finally:
        ray.shutdown()
