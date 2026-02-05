from __future__ import annotations

from typing import Any, Iterable
import base64
import tempfile

import ray
from mlx_lm import generate as mlx_generate
from mlx_lm import load as mlx_load

from .inference_actor import InferenceActor
from .schemas import MAX_CONCURRENCY, TP_SIZE, GenerateResult

MLX_VLM_MODEL_MAP = {
    "Qwen/Qwen3-VL-2B-Thinking": "mlx-community/Qwen3-VL-2B-Thinking-bf16"
}



def _apply_stop_sequences(text: str, stop: Any) -> str:
    if not stop:
        return text
    if isinstance(stop, str):
        stop_list = [stop]
    else:
        stop_list = list(stop)
    earliest = None
    for token in stop_list:
        if not token:
            continue
        idx = text.find(token)
        if idx != -1:
            earliest = idx if earliest is None else min(earliest, idx)
    if earliest is None:
        return text
    return text[:earliest]


@ray.remote(
    max_concurrency=MAX_CONCURRENCY,
    resources={"inference_node": 1},
)
class MLXActor(InferenceActor):
    def __init__(
        self,
        model: str,
        parse_model_output: Any,
        **engine_kwargs: Any,
    ):
        super().__init__(parse_model_output)
        self._max_kv_size = engine_kwargs.pop("max_kv_size", None)
        self._use_vlm = False
        self._vlm_generate = None
        self._vlm_apply_chat_template = None
        self._vlm_model = None
        self._vlm_processor = None
        self._vlm_config = None

        if model in MLX_VLM_MODEL_MAP:
            model = MLX_VLM_MODEL_MAP[model]
            try:
                from mlx_vlm import generate as vlm_generate
                from mlx_vlm import load as vlm_load
                from mlx_vlm.prompt_utils import apply_chat_template
            except ImportError as exc:
                raise RuntimeError(
                    "mlx-vlm is required for Qwen3-VL models. "
                    "Install with: pip install -U mlx-vlm"
                ) from exc
            self._vlm_model, self._vlm_processor = vlm_load(model)
            self._vlm_config = self._vlm_model.config
            self._vlm_generate = vlm_generate
            self._vlm_apply_chat_template = apply_chat_template
            self._use_vlm = True
            return

        load_kwargs: dict[str, Any] = {}
        for key in ("tokenizer_config", "model_config", "adapter_path", "lazy"):
            if key in engine_kwargs:
                load_kwargs[key] = engine_kwargs[key]
        if "trust_remote_code" in engine_kwargs:
            tokenizer_config = dict(load_kwargs.get("tokenizer_config", {}))
            tokenizer_config.setdefault(
                "trust_remote_code", engine_kwargs["trust_remote_code"]
            )
            load_kwargs["tokenizer_config"] = tokenizer_config
        self._model, self._tokenizer = mlx_load(model, **load_kwargs)

    def format_messages(self, messages: Iterable[dict[str, Any]]) -> str:
        msg_list = list(messages)
        if self._use_vlm:
            return super().format_messages(msg_list)
        try:
            return self._tokenizer.apply_chat_template(
                msg_list,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception:
            # Fallback for tiny models without a chat template.
            return super().format_messages(msg_list)

    async def generate(
        self,
        turn_refs: list[ray.ObjectRef],
        sampling: dict[str, Any],
    ) -> GenerateResult:
        """Generate model output from turns.

        Args:
            turn_refs: List of Ray ObjectRefs pointing to Turn objects
            sampling: Sampling parameters (temperature, max_tokens, etc.)

        Returns:
            GenerateResult with reasoning and content fields
        """
        # Resolve turn refs to get Turn objects
        turns = ray.get(turn_refs)

        # Convert turns to messages
        messages = self._turns_to_messages(turns)

        # Generate prompt from messages
        if self._use_vlm:
            images = self._extract_images(messages)
            prompt_text = self._messages_to_prompt(messages)
            prompt = self._vlm_apply_chat_template(
                self._vlm_processor,
                self._vlm_config,
                prompt_text,
                num_images=len(images),
            )
            sampling_kwargs = dict(sampling)
            if self._max_kv_size and "max_kv_size" not in sampling_kwargs:
                sampling_kwargs["max_kv_size"] = self._max_kv_size
            allowed_keys = {
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "repetition_penalty",
            }
            sampling_kwargs = {
                key: value
                for key, value in sampling_kwargs.items()
                if key in allowed_keys
            }
            raw_text = self._vlm_generate(
                self._vlm_model,
                self._vlm_processor,
                prompt,
                images,
                verbose=False,
                **sampling_kwargs,
            )
        else:
            prompt = self.format_messages(messages)
            sampling_kwargs = dict(sampling)
            stop = sampling_kwargs.pop("stop", None)
            if self._max_kv_size and "max_kv_size" not in sampling_kwargs:
                sampling_kwargs["max_kv_size"] = self._max_kv_size
            raw_text = mlx_generate(
                self._model,
                self._tokenizer,
                prompt=prompt,
                verbose=False,
                **sampling_kwargs,
            )
            raw_text = _apply_stop_sequences(raw_text, stop)

        # Parse raw output using the model-specific parser
        reasoning, content = self._parse_model_output(raw_text)

        return GenerateResult(reasoning=reasoning, content=content)

    def _messages_to_prompt(self, messages: Iterable[dict[str, Any]]) -> str:
        normalized = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                content = " ".join(text_parts).strip()
            normalized.append({"role": message.get("role", "user"), "content": content})
        return super().format_messages(normalized)

    def _extract_images(self, messages: Iterable[dict[str, Any]]) -> list[str]:
        images: list[str] = []
        for message in messages:
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if part.get("type") != "image_url":
                    continue
                image_url = part.get("image_url", {}).get("url", "")
                if not image_url:
                    continue
                if image_url.startswith("data:"):
                    images.append(self._write_data_image(image_url))
                else:
                    images.append(image_url)
        return images

    def _write_data_image(self, image_url: str) -> str:
        _, _, b64_data = image_url.partition(",")
        raw = base64.b64decode(b64_data)
        tmp = tempfile.NamedTemporaryFile(
            prefix="mlx_vlm_", suffix=".png", delete=False
        )
        tmp.write(raw)
        tmp.flush()
        tmp.close()
        return tmp.name
