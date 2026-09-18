"""Cable admission shared by scene and canonical connectivity writes."""

from __future__ import annotations

from typing import Any

from app.exceptions import ValidationError


def media_family(value: str) -> str:
    value = value.lower()
    if any(token in value for token in ("cat", "copper", "rj45", "punchdown")):
        return "copper"
    if any(token in value for token in ("fiber", "os", "om", "lc", "sc", "mpo", "mtp")):
        return "fiber"
    return value


def cable_policy(value: Any = None) -> dict[str, Any]:
    if value is None:
        return {"allows_cables": True, "allowed_media": ["copper", "fiber"]}
    if not isinstance(value, dict):
        return {"allows_cables": False, "allowed_media": []}
    allowed = value.get("allowed_media", [])
    return {
        "allows_cables": value.get("allows_cables") is True,
        "allowed_media": [
            medium
            for medium in ("copper", "fiber")
            if isinstance(allowed, list) and medium in allowed
        ],
    }


def require_cable_admission(value: Any, media_type: str, label: str) -> None:
    policy = cable_policy(value)
    if not policy["allows_cables"] or media_family(media_type) not in policy["allowed_media"]:
        raise ValidationError(
            f"{label} cable policy does not permit {media_family(media_type)} cables"
        )
