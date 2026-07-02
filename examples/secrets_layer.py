"""Secrets substitution layer for StackedFS.

Replaces known secret patterns (AWS keys, passwords, tokens) in file data
with substitute values so that reading sensitive files through the mount
point shows sanitized content.

Usage:
    stackedfs mount -l examples/secrets_layer.py /real/project /mnt/safe
"""

import re

SECRET_PATTERNS = [
    (re.compile(r'(AKIA[0-9A-Z]{16})'), '<AWS_ACCESS_KEY_REDACTED>'),
    (re.compile(r'(sk-[A-Za-z0-9]{32,})'), '<OPENAI_API_KEY_REDACTED>'),
    (re.compile(r'(ghp_[A-Za-z0-9]{36})'), '<GITHUB_TOKEN_REDACTED>'),
    (re.compile(r'(?i)(password\s*[=:]\s*)\S+'), r'\1***'),
    (re.compile(r'(?i)(secret\s*[=:]\s*)\S+'), r'\1***'),
    (re.compile(r'(?i)(api_key\s*[=:]\s*)\S+'), r'\1***'),
    (re.compile(r'(?i)(token\s*[=:]\s*)\S+'), r'\1***'),
]


def post_read(path: str, data: bytes) -> bytes | None:
    """Replace secret patterns in file data."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.encode("utf-8")
