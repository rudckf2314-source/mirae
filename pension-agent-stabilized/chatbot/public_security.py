"""Redact credentials and private paths at every public serialization boundary."""
from __future__ import annotations

import os
import re
from typing import Any

_AUTH = re.compile(r'(?i)\b(?:authorization\s*[\"\x27]?\s*[:=]\s*[\"\x27]?\s*)?(?:bearer|basic)\s+[^\s\"\x27,;}]+')
_KEY = re.compile(r'(?i)\b(?:clova_studio_api_key|law_api_oc|api[_ -]?key|authorization|password)\b[\"\x27]?\s*[:=]\s*[\"\x27]?[^\s\"\x27,;}]+')
_WINDOWS = re.compile(r'(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\n\r\"<>|]+')
_UNIX = re.compile(r'(?<![\w:/])/(?:home|root|usr|var|tmp|app|workspace|opt|Users)(?:/[^\s\"\x27<>]+)+')
_PRIVATE_KEYS = {'raw_answer', 'internal_sources', 'authorization', 'clova_studio_api_key', 'law_api_oc', 'password'}


def redact_text(value: Any) -> str:
    text = str(value or '')
    for key in ('CLOVA_STUDIO_API_KEY', 'LAW_API_OC'):
        secret = os.getenv(key, '')
        if len(secret) >= 4:
            text = text.replace(secret, '[redacted]')
    text = _AUTH.sub('[redacted]', text)
    text = _KEY.sub('[redacted]', text)
    text = _WINDOWS.sub('[internal path]', text)
    return _UNIX.sub('[internal path]', text)


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: public_payload(v) for k, v in value.items() if str(k).lower() not in _PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [public_payload(v) for v in value]
    return redact_text(value) if isinstance(value, str) else value
