def qwen3_response_parser(raw_output: str) -> tuple[str | None, str]:
    if "</think>" in raw_output:
        reasoning, _, content = raw_output.partition("</think>")
        return (reasoning.strip() or None, content.strip())
    else:
        return (None, raw_output)
