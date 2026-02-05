from __future__ import annotations

from typing import Any, Iterable
from uuid import uuid4

import ray
from transformers import AutoTokenizer
from vllm import AsyncLLMEngine, SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs

from .inference_actor import InferenceActor
from .schemas import MAX_CONCURRENCY, TP_SIZE, GenerateResult


@ray.remote(
    max_concurrency=MAX_CONCURRENCY,
    resources={"inference_node": 1},
)
class VLLMActor(InferenceActor):
    def __init__(self, model: str, parse_model_output: Any, **engine_kwargs: Any):
        super().__init__(parse_model_output)
        engine_args = AsyncEngineArgs(
            model=model,
            tensor_parallel_size=TP_SIZE,
            **engine_kwargs,
        )
        self._engine = AsyncLLMEngine.from_engine_args(engine_args)
        self._tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=True)

    def format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        msg_list = list(messages)
        try:
            return self._tokenizer.apply_chat_template(
                msg_list,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:
            return super().format_messages(msg_list)

    async def generate(
        self,
        turn_refs: list[ray.ObjectRef],
        sampling: dict[str, Any],
    ) -> GenerateResult:
        turns = ray.get(turn_refs)
        messages = self._turns_to_messages(turns)
        prompt = self.format_messages(messages)
        params = SamplingParams(**sampling)
        request_id = uuid4().hex
        raw_text = ""
        async for output in self._engine.generate(
            prompt, params, request_id=request_id
        ):
            if output.outputs:
                raw_text = output.outputs[0].text

        reasoning, content = self._parse_model_output(raw_text)
        return GenerateResult(reasoning=reasoning, content=content)
