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


def validate(corpus_dir: Path) -> int:
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema)

    incident_files = sorted(corpus_dir.glob("*.json"))
    if not incident_files:
        print(f"no incident files found in {corpus_dir}", file=sys.stderr)
        return 1

    errors = 0
    seen_classes: set[str] = set()
    seen_ids: set[str] = set()

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

    print(f"checked {len(incident_files)} incident(s), {errors} schema error(s)")

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
