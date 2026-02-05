from dataclasses import dataclass

TP_SIZE = 1
MAX_CONCURRENCY = 128


@dataclass
class GenerateResult:
    reasoning: str | None
    content: str
