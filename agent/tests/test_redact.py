"""T7 validation: 100% on the seeded secret corpus — not 99%."""

from guardia_agent.redact import Redactor, redact_evidence, redact_node

# One representative real-shaped secret per class the spec calls out.
# (Values are fabricated, not real credentials.)
SEEDED_SECRETS: dict[str, str] = {
    "JWT": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3F8oPPtNTxvXTQI",
    "AWS-ACCESS-KEY": "AKIAIOSFODNN7EXAMPLE",
    "AWS-SECRET-KEY": "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ML-ACCESS-TOKEN": "APP_USR-1234567890123456-060100-abcdef1234567890abcdef1234567890-123456789",
    "ML-REFRESH-TOKEN": "TG-5f4dcc3b5aa765d61d8327deb882cf99-123456789",
    "ACCOUNT-ID": "arn:aws:lambda:sa-east-1:727990090900:function:photolist-analyze-photo",
    "EMAIL": "operator@photolist-latam.com",
    "UY-CI": "1.234.567-8",
    "PHONE": "+598 99 123 456",
}

# The raw secret substring that must never survive redaction for each class
# (distinct from the seed line in cases like ACCOUNT-ID/AWS-SECRET-KEY where
# only part of the line is the actual secret).
SECRET_VALUE: dict[str, str] = {
    "JWT": SEEDED_SECRETS["JWT"],
    "AWS-ACCESS-KEY": SEEDED_SECRETS["AWS-ACCESS-KEY"],
    "AWS-SECRET-KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ML-ACCESS-TOKEN": SEEDED_SECRETS["ML-ACCESS-TOKEN"],
    "ML-REFRESH-TOKEN": SEEDED_SECRETS["ML-REFRESH-TOKEN"],
    "ACCOUNT-ID": "727990090900",
    "EMAIL": SEEDED_SECRETS["EMAIL"],
    "UY-CI": SEEDED_SECRETS["UY-CI"],
    "PHONE": "99 123 456",  # the significant-digits portion, regardless of exact spacing
}


def test_every_seeded_secret_class_is_fully_redacted():
    redactor = Redactor()
    leaks = []
    for label, line in SEEDED_SECRETS.items():
        redacted = redactor.redact(line)
        secret = SECRET_VALUE[label]
        if secret in redacted:
            leaks.append((label, redacted))
    assert leaks == [], f"secret(s) leaked past redaction: {leaks}"


def test_every_seeded_secret_produces_a_placeholder():
    redactor = Redactor()
    for label, line in SEEDED_SECRETS.items():
        redacted = redactor.redact(line)
        assert "[REDACTED-" in redacted, f"{label} produced no placeholder: {redacted!r}"


def test_redaction_embedded_in_a_realistic_log_line_still_catches_every_class():
    redactor = Redactor()
    log_line = (
        "2026-06-12T03:14:07Z ERROR token refresh failed for operator@photolist-latam.com "
        "(CI 1.234.567-8, phone +598 99 123 456) using access_token=APP_USR-1234567890123456-060100-"
        "abcdef1234567890abcdef1234567890-123456789 refresh_token=TG-5f4dcc3b5aa765d61d8327deb882cf99-123456789 "
        "auth=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3F8oPPtNTxvXTQI "
        "role=arn:aws:iam::727990090900:role/lambda-exec"
    )
    redacted = redactor.redact(log_line)
    for label, secret in SECRET_VALUE.items():
        assert secret not in redacted, f"{label} leaked in combined log line: {redacted!r}"


def test_placeholders_are_stable_across_repeated_occurrences():
    redactor = Redactor()
    text = f"first: {SEEDED_SECRETS['EMAIL']} again: {SEEDED_SECRETS['EMAIL']}"
    redacted = redactor.redact(text)
    first, second = redacted.split("again:")
    placeholder = first.split("first: ")[1].strip()
    assert placeholder in second
    assert redacted.count(placeholder) == 2


def test_placeholders_differ_across_distinct_values_of_the_same_class():
    redactor = Redactor()
    text = "a@example.com and b@example.com"
    redacted = redactor.redact(text)
    assert "[REDACTED-EMAIL-1]" in redacted
    assert "[REDACTED-EMAIL-2]" in redacted


def test_operational_data_is_not_mistaken_for_a_secret():
    redactor = Redactor()
    line = "duration=1234.56ms status=200 python=3.12.10 ip=10.0.1.5 request_id=abc-123"
    redacted = redactor.redact(line)
    assert redacted == line


# --- Evidence grounding survives redaction (T7's second required test) ---


def test_redact_evidence_leaves_ref_untouched_so_citations_still_resolve():
    evidence = [
        {
            "type": "log",
            "ref": "query:abcd1234:line-17",
            "excerpt": f"user email {SEEDED_SECRETS['EMAIL']} triggered the retry",
        }
    ]
    redacted = redact_evidence(evidence)

    assert redacted[0]["ref"] == "query:abcd1234:line-17"
    assert SEEDED_SECRETS["EMAIL"] not in redacted[0]["excerpt"]
    assert "[REDACTED-EMAIL-1]" in redacted[0]["excerpt"]


def test_redact_node_is_idempotent_and_keeps_a_single_stable_redactor_across_calls():
    state = {
        "evidence": [{"type": "log", "ref": "q:1", "excerpt": SEEDED_SECRETS["EMAIL"]}],
    }
    state_after_first = redact_node(state)
    placeholder = state_after_first["evidence"][0]["excerpt"]

    state_after_first["evidence"].append({"type": "log", "ref": "q:2", "excerpt": SEEDED_SECRETS["EMAIL"]})
    state_after_second = redact_node(state_after_first)

    assert state_after_second["evidence"][0]["excerpt"] == placeholder
    assert state_after_second["evidence"][1]["excerpt"] == placeholder
