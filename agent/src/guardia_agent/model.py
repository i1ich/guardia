"""The real chat model for classify and hypothesize (T9/T10).

Provider: the Anthropic API directly (operator decision 2026-10-06). The model
id comes from SSM `/guardia/model` (same model as the LeaseLens contract
analysis, claude-sonnet-5). The API key comes from SSM SecureString
`/guardia/anthropic-api-key`, placed by the operator; it is read once per
process and never logged or put into graph state.

Spend guards, mirroring LeaseLens (a per-call `max_tokens`) plus the
per-incident token budget enforced in investigate.py. A provider-side hard
limit on the key is the operator's to set; code cannot enforce it.
"""

from __future__ import annotations

import boto3

REGION = "sa-east-1"
MODEL_PARAM = "/guardia/model"
API_KEY_PARAM = "/guardia/anthropic-api-key"
MAX_TOKENS_PER_CALL = 4000
REQUEST_TIMEOUT_SECONDS = 60


class ModelNotConfigured(RuntimeError):
    """The API key (or model id) is not in SSM yet."""


def _ssm_value(ssm, name: str, decrypt: bool = False) -> str:
    try:
        return ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound as exc:
        raise ModelNotConfigured(f"SSM parameter {name} not found") from exc


def build_chat_model(ssm=None):
    """Returns a ChatAnthropic bound to no tools. Raises ModelNotConfigured if SSM lacks the key."""
    from langchain_anthropic import ChatAnthropic

    ssm = ssm or boto3.client("ssm", region_name=REGION)
    return ChatAnthropic(
        model=_ssm_value(ssm, MODEL_PARAM),
        api_key=_ssm_value(ssm, API_KEY_PARAM, decrypt=True),
        max_tokens=MAX_TOKENS_PER_CALL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        temperature=0,
        max_retries=2,
    )
