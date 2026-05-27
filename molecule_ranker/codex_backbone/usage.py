from __future__ import annotations

import re
from typing import Any


def extract_usage_summary(stdout: str, stderr: str) -> dict[str, Any]:
    text = f"{stdout}\n{stderr}"
    usage: dict[str, Any] = {}
    token_matches = re.findall(r"(?i)\b(input|output|total)[_-]?tokens?\b\D+(\d+)", text)
    for label, value in token_matches:
        usage[f"{label.lower()}_tokens"] = int(value)
    if "rate limit" in text.lower():
        usage["rate_limit_observed"] = True
    return usage
