"""Retrieval eval for search_runbook (T17): hit@k on labelled queries, plus
whether no-match queries correctly return nothing.

Usage:
    python evals/retrieval_eval.py <runbooks-dir> [--queries evals/retrieval_queries.json]

The runbook sources are private (guardia-private/runbooks), so this runs
locally or in a job that can reach them, not in public CI. Exit code is
non-zero if any non-stress query misses the top 2 or any no-match query
returns a result.

Caveat recorded with the numbers: the aliases and the queries were written
by the same person, so hit@k here is an optimistic upper bound. Add real
incident phrasings to the query file as they occur.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_runbook_index import load_runbooks  # noqa: E402
from tools.read._bm25 import build_index, search  # noqa: E402

DEFAULT_QUERIES = Path(__file__).with_name("retrieval_queries.json")


def evaluate(runbooks_dir: Path, queries_path: Path) -> dict:
    index = build_index(load_runbooks(runbooks_dir))
    queries = json.loads(queries_path.read_text(encoding="utf-8"))

    positives, negatives = [], []
    for q in queries:
        names = [h["name"] for h in search(index, q["query"], k=3)]
        if q["expect"]:
            rank = next((i + 1 for i, n in enumerate(names) if n in q["expect"]), None)
            positives.append({**q, "returned": names, "rank": rank})
        else:
            negatives.append({**q, "returned": names})

    def hit_at(k: int) -> float:
        return sum(1 for p in positives if p["rank"] and p["rank"] <= k) / len(positives)

    return {
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "hit@1": hit_at(1),
        "hit@2": hit_at(2),
        "hit@3": hit_at(3),
        "false_positives": [n for n in negatives if n["returned"]],
        "misses": [p for p in positives if not p["rank"] or p["rank"] > 2],
        "positives": positives,
        "negatives": negatives,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runbooks_dir", type=Path)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    args = parser.parse_args()

    r = evaluate(args.runbooks_dir, args.queries)
    print(f"positive queries: {r['n_positive']}  hit@1={r['hit@1']:.2f} hit@2={r['hit@2']:.2f} hit@3={r['hit@3']:.2f}")
    print(f"no-match queries: {r['n_negative']}  false positives: {len(r['false_positives'])}")
    for p in r["positives"]:
        print(f"  {'OK ' if p['rank'] and p['rank'] <= 2 else 'MISS'} rank={p['rank']} [{p['class']}] {p['query']!r} -> {p['returned']}")
    for n in r["negatives"]:
        print(f"  {'OK ' if not n['returned'] else 'FALSE-POSITIVE'} [{n['class']}] {n['query']!r} -> {n['returned']}")

    hard_misses = [m for m in r["misses"] if "stress test" not in m.get("note", "")]
    sys.exit(1 if hard_misses or r["false_positives"] else 0)


if __name__ == "__main__":
    main()
