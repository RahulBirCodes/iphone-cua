"""XML action parser for extracting structured actions from model output.

This module parses raw model completions that follow the Qwen3-VL format:
- The chat template opens a <think> tag in the prompt
- The model outputs reasoning text and closes with </think>
- After </think>, the model outputs a single self-closing XML action tag

Supported actions:
- tap(x: float, y: float)
- long_press(x: float, y: float)
- swipe(start_x: float, start_y: float, end_x: float, end_y: float)
- type_text(text: str)
- go_home()
- wait()
- fail()
- finished()
"""

import re
from typing import Any

from ..schemas import ParsedOutput


# Action schemas: action_name -> (required_params, param_validators)
ALLOWED_ACTIONS: dict[str, tuple[list[str], dict[str, Any]]] = {
    "tap": (
        ["x", "y"],
        {"x": ("float", 0.0, 1.0), "y": ("float", 0.0, 1.0)},
    ),
    "long_press": (
        ["x", "y"],
        {"x": ("float", 0.0, 1.0), "y": ("float", 0.0, 1.0)},
    ),
    "swipe": (
        ["start_x", "start_y", "end_x", "end_y"],
        {
            "start_x": ("float", 0.0, 1.0),
            "start_y": ("float", 0.0, 1.0),
            "end_x": ("float", 0.0, 1.0),
            "end_y": ("float", 0.0, 1.0),
        },
    ),
    "type_text": (["text"], {"text": ("str",)}),
    "go_home": ([], {}),
    "wait": ([], {}),
    "fail": ([], {}),
    "finished": ([], {}),
}


def parse(raw_output: str) -> ParsedOutput:
    """Parse raw model output into structured ParsedOutput.

    The model output is expected to contain:
    1. Optional reasoning text (closed with </think>)
    2. Exactly one self-closing XML action tag

    Args:
        raw_output: Raw model completion string

    Returns:
        ParsedOutput with reasoning, content, and action dict
    """
    # Step 1: Strip special tokens and whitespace
    text = raw_output.strip()
    for end_marker in ["<|im_end|>", "<|endoftext|>", "</s>"]:
        text = text.replace(end_marker, "")
    text = text.strip()

    # Step 2: Split reasoning vs action region
    reasoning_text: str
    action_region: str

    if "</think>" in text:
        parts = text.split("</think>", 1)
        reasoning_text = parts[0].strip()
        if reasoning_text.startswith("<think>"):
            reasoning_text = reasoning_text[7:].strip()
        action_region = parts[1].strip()
    else:
        reasoning_text = ""
        action_region = text

    # Step 3: Extract exactly one action tag
    action_pattern = r"<(" + "|".join(ALLOWED_ACTIONS.keys()) + r")\s*([^>]*?)\s*/>"
    matches = list(re.finditer(action_pattern, action_region))

    if len(matches) == 0:
        return ParsedOutput(
            reasoning=reasoning_text,
            content=action_region,
            action=None,
        )

    if len(matches) > 1:
        return ParsedOutput(
            reasoning=reasoning_text,
            content=action_region,
            action=None,
        )

    # Exactly one match found
    match = matches[0]
    tag_name = match.group(1)
    attrs_text = match.group(2)
    matched_tag_string = match.group(0)

    # Step 4: Parse and validate attributes
    attributes = _parse_attributes(attrs_text)
    required_params, validators = ALLOWED_ACTIONS[tag_name]

    for param in required_params:
        if param not in attributes:
            return ParsedOutput(
                reasoning=reasoning_text,
                content=matched_tag_string,
                action=None,
            )

    validated_params: dict[str, Any] = {}
    for param_name, param_value in attributes.items():
        if param_name not in validators:
            continue

        validator = validators[param_name]
        param_type = validator[0]

        try:
            if param_type == "float":
                min_val, max_val = validator[1], validator[2]
                val = float(param_value)
                if not (min_val <= val <= max_val):
                    return ParsedOutput(
                        reasoning=reasoning_text,
                        content=matched_tag_string,
                        action=None,
                    )
                validated_params[param_name] = val
            elif param_type == "str":
                validated_params[param_name] = str(param_value)
        except (ValueError, TypeError):
            return ParsedOutput(
                reasoning=reasoning_text,
                content=matched_tag_string,
                action=None,
            )

    return ParsedOutput(
        reasoning=reasoning_text,
        content=matched_tag_string,
        action={"action": tag_name, "params": validated_params},
    )


def _parse_attributes(attrs_text: str) -> dict[str, str]:
    """Parse XML attributes from attribute string."""
    attributes: dict[str, str] = {}
    attr_pattern = r'(\w+)\s*=\s*["\']([^"\']*)["\']'
    for match in re.finditer(attr_pattern, attrs_text):
        attributes[match.group(1)] = match.group(2)
    return attributes
