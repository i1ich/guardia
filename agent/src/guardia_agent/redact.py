"""The redact node (T7) — the outbound half of Guardia's security posture.

Placed on every path into a model call. Strips secret-shaped substrings
from evidence text before it can reach the model, replacing each with a
*stable* placeholder rather than deleting it: the same secret value maps
to the same placeholder everywhere within one `Redactor` instance, so
citations, cross-references between evidence items, and "is this the same
token as before" reasoning all survive redaction intact. What never
survives is the secret itself.

Redaction only ever touches the text handed to the model (an evidence
item's `excerpt`) — it never touches `ref`, the retrievable pointer back
to the source line. That separation is what lets M3 (evidence grounding)
hold even though the model only ever sees scrubbed text: a citation
resolves through `ref` to the original tool call, not through the
(redacted) excerpt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class _Pattern:
    label: str
    regex: re.Pattern[str]
    group: int = 0  # which regex group to redact; 0 = the whole match


_PATTERNS: list[_Pattern] = [
    # JWTs — three dot-separated base64url segments, header starts with "eyJ".
    _Pattern("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    # AWS access key IDs (permanent + temporary/STS).
    _Pattern("AWS-ACCESS-KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # AWS secret access keys — only redacted when adjacent to a recognizable
    # label, since a bare 40-char base64 string is otherwise indistinguishable
    # from any other opaque token and would drown the corpus in false positives.
    _Pattern(
        "AWS-SECRET-KEY",
        re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
        group=1,
    ),
    # MercadoLibre / MercadoPago OAuth access tokens: APP_USR-<client>-<ts>-<hash>-<user>.
    _Pattern("ML-ACCESS-TOKEN", re.compile(r"\bAPP_USR-[A-Za-z0-9-]+\b")),
    # MercadoLibre / MercadoPago OAuth refresh tokens: TG-<hash>-<user>.
    _Pattern("ML-REFRESH-TOKEN", re.compile(r"\bTG-[A-Za-z0-9]+-[0-9]+\b")),
    # AWS account id embedded inside an ARN — redact only the account
    # segment so the resource type/name/region stay visible for grounding.
    # (Python's re requires fixed-width lookbehind, so the ARN prefix is a
    # capture group rather than a lookbehind; group 2 is the account id.)
    _Pattern(
        "ACCOUNT-ID",
        re.compile(r"\b(arn:aws[a-z0-9-]*:[a-z0-9-]+:[a-z0-9-]*:)(\d{12})(?=:)"),
        group=2,
    ),
    # Emails.
    _Pattern("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Uruguayan Cédula de Identidad: "1.234.567-8" or "1234567-8" (7-8 digits + check digit).
    _Pattern("UY-CI", re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-\d\b|\b\d{7,8}-\d\b")),
    # Phone numbers: international "+<country><number>", or Uruguayan
    # mobile "09X XXX XXX". Deliberately narrow — a broad digit-grouping
    # regex here would also eat IPs, durations, and version strings, which
    # are exactly the operational data an incident hypothesis needs.
    _Pattern("PHONE", re.compile(r"\+\d{1,3}(?:[ .-]?\d{2,4}){2,4}\b|\b09\d(?:[ .-]?\d{3}){2}\b")),
]


@dataclass
class Redactor:
    """Stateful across one incident run: the same secret value always maps
    to the same placeholder, no matter how many evidence items it appears in."""

    _placeholders: dict[tuple[str, str], str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def _placeholder_for(self, label: str, value: str) -> str:
        key = (label, value)
        if key not in self._placeholders:
            self._counters[label] = self._counters.get(label, 0) + 1
            self._placeholders[key] = f"[REDACTED-{label}-{self._counters[label]}]"
        return self._placeholders[key]

    def redact(self, text: str) -> str:
        if not text:
            return text
        for pattern in _PATTERNS:
            def _sub(match: re.Match[str], pattern=pattern) -> str:
                value = match.group(pattern.group)
                placeholder = self._placeholder_for(pattern.label, value)
                if pattern.group == 0:
                    return placeholder
                # Redact only the captured group, keep the surrounding match text.
                start, end = match.span(pattern.group)
                return match.group(0)[: start - match.start()] + placeholder + match.group(0)[end - match.start() :]

            text = pattern.regex.sub(_sub, text)
        return text


def redact_evidence(evidence: list[dict], redactor: Redactor | None = None) -> list[dict]:
    """Redact every evidence item's `excerpt` in place (new list, new dicts);
    `ref` — the citation's retrievable pointer — is passed through untouched."""
    redactor = redactor or Redactor()
    redacted = []
    for item in evidence:
        new_item = dict(item)
        if "excerpt" in new_item and new_item["excerpt"]:
            new_item["excerpt"] = redactor.redact(new_item["excerpt"])
        redacted.append(new_item)
    return redacted


def redact_node(state: dict) -> dict:
    """LangGraph node: redacts every evidence excerpt in `state["evidence"]`
    before any node downstream of this one is allowed to call a model."""
    redactor: Redactor = state.get("_redactor") or Redactor()
    return {
        **state,
        "evidence": redact_evidence(state.get("evidence", []), redactor),
        "_redactor": redactor,
    }
