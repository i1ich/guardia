"""Build the T17 runbook search index from a directory of markdown runbooks.

Usage:
    python scripts/build_runbook_index.py <runbooks-dir> [--out index.json] [--upload-bucket BUCKET]

Each runbook is a markdown file with optional YAML frontmatter:
    name, description, aliases (list of phrases an incident might use).
The runbook sources live in guardia-private; only the index is uploaded, to
the private Guardia bucket at index/bm25.json. Run it in CI whenever the
sources change.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.read._bm25 import build_index  # noqa: E402
from tools.read.runbook import INDEX_KEY  # noqa: E402

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def load_runbooks(directory: Path) -> list[dict]:
    runbooks = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(text)
        meta = yaml.safe_load(match.group(1)) if match else {}
        body = text[match.end():] if match else text
        runbooks.append(
            {
                "name": meta.get("name") or path.stem,
                "description": meta.get("description", ""),
                "aliases": meta.get("aliases", []),
                "body": body,
            }
        )
    return runbooks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runbooks_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("bm25.json"))
    parser.add_argument("--upload-bucket", help="also upload to s3://BUCKET/" + INDEX_KEY)
    args = parser.parse_args()

    runbooks = load_runbooks(args.runbooks_dir)
    if not runbooks:
        sys.exit(f"no *.md runbooks in {args.runbooks_dir}")
    index = build_index(runbooks)
    payload = json.dumps(index, ensure_ascii=False)
    args.out.write_text(payload, encoding="utf-8")
    print(f"indexed {len(runbooks)} runbooks -> {args.out} ({len(payload)} bytes)")

    if args.upload_bucket:
        import boto3

        boto3.client("s3", region_name="sa-east-1").put_object(
            Bucket=args.upload_bucket, Key=INDEX_KEY, Body=payload.encode("utf-8"), ContentType="application/json"
        )
        print(f"uploaded s3://{args.upload_bucket}/{INDEX_KEY}")


if __name__ == "__main__":
    main()
