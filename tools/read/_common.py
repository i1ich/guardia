"""Shared constants and helpers for the T6 read-only tools.

Every tool in this package is bounded on two axes: result size (so a
single tool call can't blow the per-incident token budget from T10) and
field shape (so a log line that happens to carry a large embedded
payload — a base64 image, a document blob — can't reach the model). Both
bounds live here so every tool enforces them the same way.
"""

from __future__ import annotations

import re

import boto3

REGION = "sa-east-1"

MAX_LOG_LINES = 200
MAX_LINE_CHARS = 4000
MAX_TOTAL_LOG_CHARS = 100_000
MAX_METRIC_DATAPOINTS = 500
MAX_STACK_EVENTS = 100
MAX_STACK_RESOURCES = 500

# A run of 200+ base64-alphabet characters is almost certainly an embedded
# binary payload (image, document) rather than an operational log message.
_BINARY_BLOB_PATTERN = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
_BLOB_PLACEHOLDER = "[binary-payload-omitted]"


def strip_binary_payloads(text: str) -> str:
    """Replace long base64-looking runs with a placeholder, preserving the surrounding line."""
    return _BINARY_BLOB_PATTERN.sub(_BLOB_PLACEHOLDER, text)


def bound_line(text: str) -> str:
    text = strip_binary_payloads(text)
    if len(text) > MAX_LINE_CHARS:
        return text[:MAX_LINE_CHARS] + "...[truncated]"
    return text


def client(service: str):
    return boto3.client(service, region_name=REGION)
