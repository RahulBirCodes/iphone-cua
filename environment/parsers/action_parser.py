"""
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


ALLOWED_ACTIONS: tuple[str, ...] = (
    "tap",
    "long_press",
    "swipe",
    "type_text",
    "go_home",
    "wait",
    "fail",
    "finished",
)


def parse(content: str) -> dict[str, Any] | None:
    """Parse clean content string to extract XML action tag.

    The content string is expected to be pre-processed (reasoning already
    separated by the inference actor's parse_model_output callable).

    Args:
        content: Clean content string (just the action region)

    Returns:
        Action dict {"action": name, "params": parsed_attrs} or None on failure
    """
    # Step 1: Strip whitespace
    text = content.strip()

    # Step 2: Extract exactly one action tag
    action_pattern = r"<(" + "|".join(ALLOWED_ACTIONS) + r")\s*([^>]*?)\s*/>"
    matches = list(re.finditer(action_pattern, text))

    if len(matches) == 0:
        # No action tag found
        return None

    if len(matches) > 1:
        # Multiple action tags found
        return None

    # Exactly one match found
    match = matches[0]
    tag_name = match.group(1)
    attrs_text = match.group(2)

    # Step 3: Parse attributes without validation.
    attributes = _parse_attributes(attrs_text)
    return {"action": tag_name, "params": attributes}


def _parse_attributes(attrs_text: str) -> dict[str, str]:
    """Parse XML attributes from attribute string."""
    attributes: dict[str, str] = {}
    attr_pattern = r'(\w+)\s*=\s*["\']([^"\']*)["\']'
    for match in re.finditer(attr_pattern, attrs_text):
        attributes[match.group(1)] = match.group(2)
    return attributes
