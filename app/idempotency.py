"""Request normalization and hashing for idempotency (design D6).

Two requests are equivalent when their hashes match. The canonical form uses
only the request itself: no template defaults, nothing from the deployed
template, so a replay never depends on what is deployed now.
"""

import hashlib
import json
from typing import Any

from app.schemas import EmailRequest, SmsRequest


def normalize_json(value: Any) -> Any:
    """Make equal JSON values compare equal: integral floats become integers.

    ``True`` stays distinct from ``1``. Key order is handled at serialization.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: normalize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_json(v) for v in value]
    return value


def request_hash(request: EmailRequest | SmsRequest) -> str:
    canonical = {
        "channel": request.channel,
        "template_slug": request.template_slug,
        "recipient": request.recipient,
        "template_options": normalize_json(request.template_options),
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
