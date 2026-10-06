"""Validate the incident corpus against evals/schema/incident.schema.json.

Usage:
    python evals/validate_corpus.py <path-to-corpus-dir>

The corpus itself lives outside this repository (see README.md) since it
contains operational details about the subject systems. This script only
needs a directory of *.json incident files to check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parent / "schema" / "incident.schema.json"

REQUIRED_INCIDENT_CLASSES = {
    "ml-api-403-search",
    "ml-token-expiry",
    "deploy-regression",
    "lambda-timeout-cold-start",
    "cost-throttling-anomaly",
}
MIN_INCIDENTS = 6
# Evidence the T6/T17 tools can fetch. An incident with none of these cannot
# satisfy M3 (every claim cites retrievable evidence) and is excluded from
# M3 and held-out scoring; it still serves classification tests.
RETRIEVABLE_EVIDENCE_TYPES = {"log", "metric", "stack_event", "runbook"}


def m3_eligible(record: dict) -> bool:
    return any(e.get("type") in RETRIEVABLE_EVIDENCE_TYPES for e in record.get("evidence", []))


def validate(corpus_dir: Path) -> int:
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)

    # _placeholder-* files only prove the schema shape; they are not incidents
    # and must not count toward MIN_INCIDENTS or class coverage.
    incident_files = sorted(
        p for p in corpus_dir.glob("*.json") if not p.name.startswith("_placeholder-")
    )
    if not incident_files:
        print(f"no incident files found in {corpus_dir}", file=sys.stderr)
        return 1

    errors = 0
    seen_classes: set[str] = set()
    seen_ids: set[str] = set()
    ineligible: list[str] = []

    for path in incident_files:
        record = json.loads(path.read_text())
        for error in sorted(validator.iter_errors(record), key=str):
            errors += 1
            print(f"{path.name}: {error.message} (at {'/'.join(map(str, error.path))})")
        incident_id = record.get("incident_id")
        if incident_id in seen_ids:
            errors += 1
            print(f"{path.name}: duplicate incident_id '{incident_id}'")
        seen_ids.add(incident_id)
        seen_classes.add(record.get("incident_class"))
        if not m3_eligible(record):
            ineligible.append(str(incident_id))

    print(f"checked {len(incident_files)} incident(s), {errors} schema error(s)")

    if ineligible:
        print(f"not M3/held-out eligible (no retrievable evidence): {ineligible}")

    missing_classes = REQUIRED_INCIDENT_CLASSES - seen_classes
    if missing_classes:
        print(f"missing incident classes: {sorted(missing_classes)}")

    if len(incident_files) < MIN_INCIDENTS:
        print(
            f"only {len(incident_files)} incident(s); T2 target is >= {MIN_INCIDENTS}"
        )

    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_dir", type=Path)
    args = parser.parse_args()
    sys.exit(validate(args.corpus_dir))


if __name__ == "__main__":
    main()
