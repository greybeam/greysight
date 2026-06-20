"""Server-side work-email gate. The free-provider list is the shared canonical
fixture so it cannot drift from the web client's copy."""

from __future__ import annotations

import json
import re
from pathlib import Path

_FIXTURE = (
    Path(__file__).resolve().parents[4] / "shared" / "free-email-domains.json"
)

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    json.loads(_FIXTURE.read_text())
)

# Mirrors EMAIL_PATTERN in apps/web/src/lib/work-email.ts: non-empty local part,
# exactly one "@", dotted domain with each label non-empty.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")


def is_work_email(email: str) -> bool:
    normalized = email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(normalized):
        return False
    domain = normalized[normalized.index("@") + 1 :]
    return domain not in FREE_EMAIL_DOMAINS
