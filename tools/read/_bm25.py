"""Dependency-free BM25 over a handful of runbooks (T17).

Pure stdlib on purpose: the index is built offline by
scripts/build_runbook_index.py and only *scored* inside the Lambda, so the
runtime needs no ML package. T4 measured ~3s of cold-start cost just from
importing langgraph/pydantic; another heavy import is not affordable.

Two behaviours matter more than ranking quality at this corpus size
(five documents):

* **Empty beats wrong.** A runbook is only returned if it covers enough of
  the query: the IDF mass of the query terms it contains, divided by the
  IDF mass of *all* query terms, including words the corpus has never seen.
  Unknown words are the strongest evidence that the query is about
  something the runbooks do not cover, so they count against a match. A
  confidently wrong retrieval is worse than none.

A corpus-derived stop list (drop terms present in most documents) was tried
first and removed: with five runbooks, three of them about MercadoLibre,
it discarded "token", "403", "search" and "expired" — exactly the words
that distinguish the runbooks.

The same `tokenize` runs at build time and query time, so they cannot drift.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

INDEX_VERSION = 1
K1 = 1.5
B = 0.75
# Term frequency multipliers per field: a match in the title/aliases is a
# much stronger signal than a match somewhere in a 6 KB procedure.
FIELD_WEIGHTS = {"name": 3, "aliases": 3, "description": 2, "body": 1}
MIN_COVERAGE = 0.4
MAX_SECTION_CHARS = 1200

_STOPWORDS = frozenset(
    "a an and are as at be by for from how in is it not of on or that the this to was we what when with "
    "de el la los las un una y o en que por para con del se su al es".split()
)
_WORD = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    # Deliberately crude (plural / -ed / -ing): it only has to be applied
    # identically to documents and queries.
    if len(token) > 4 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return [_stem(t) for t in _WORD.findall(folded) if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())]


def _weighted_tf(fields: dict[str, str]) -> dict[str, float]:
    tf: dict[str, float] = {}
    for field, text in fields.items():
        weight = FIELD_WEIGHTS[field]
        for token in tokenize(text):
            tf[token] = tf.get(token, 0.0) + weight
    return tf


def split_sections(body: str) -> list[dict[str, str]]:
    """Split a markdown body on headings so a hit can show the relevant part."""
    sections: list[dict[str, str]] = []
    heading, lines = "", []
    for line in body.splitlines():
        if re.match(r"^#{1,3} ", line):
            if lines:
                sections.append({"heading": heading, "text": "\n".join(lines).strip()[:MAX_SECTION_CHARS]})
            heading, lines = line.lstrip("# ").strip(), []
        else:
            lines.append(line)
    if lines:
        sections.append({"heading": heading, "text": "\n".join(lines).strip()[:MAX_SECTION_CHARS]})
    return [s for s in sections if s["text"]]


def build_index(runbooks: list[dict[str, Any]]) -> dict[str, Any]:
    """`runbooks`: [{"name", "description", "aliases": [...], "body"}] -> JSON-able index."""
    docs = []
    for rb in runbooks:
        fields = {
            "name": rb["name"].replace("-", " "),
            "aliases": " . ".join(rb.get("aliases", [])),
            "description": rb.get("description", ""),
            "body": rb["body"],
        }
        tf = _weighted_tf(fields)
        docs.append(
            {
                "name": rb["name"],
                "description": " ".join(rb.get("description", "").split()),
                "aliases": rb.get("aliases", []),
                "length": sum(tf.values()),
                "tf": tf,
                "sections": split_sections(rb["body"]),
            }
        )
    df: dict[str, int] = {}
    for doc in docs:
        for term in doc["tf"]:
            df[term] = df.get(term, 0) + 1
    n = len(docs)
    return {
        "version": INDEX_VERSION,
        "doc_count": n,
        "avg_length": sum(d["length"] for d in docs) / n if n else 0.0,
        "df": df,
        "docs": docs,
    }


def _idf(index: dict[str, Any], term: str) -> float:
    n, df = index["doc_count"], index["df"].get(term, 0)
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def _best_section(doc: dict[str, Any], terms: set[str]) -> dict[str, str] | None:
    best, best_hits = None, 0
    for section in doc["sections"]:
        hits = len(terms & set(tokenize(section["heading"] + " " + section["text"])))
        if hits > best_hits:
            best, best_hits = section, hits
    return best


def search(
    index: dict[str, Any], query: str, k: int = 3, min_matched: int = 2, min_coverage: float = MIN_COVERAGE
) -> list[dict[str, Any]]:
    """Top-k runbooks for `query`, best first; [] when nothing clears the bar."""
    if index.get("version") != INDEX_VERSION:
        raise ValueError(f"unsupported runbook index version {index.get('version')!r}")
    terms = set(tokenize(query))
    if not terms:
        return []
    required = min(min_matched, len(terms))
    avg = index["avg_length"] or 1.0
    total_mass = sum(_idf(index, t) for t in terms)

    hits = []
    for doc in index["docs"]:
        matched = sorted(t for t in terms if doc["tf"].get(t))
        if len(matched) < required:
            continue
        coverage = sum(_idf(index, t) for t in matched) / total_mass
        if coverage < min_coverage:
            continue
        score = 0.0
        for term in matched:
            tf = doc["tf"][term]
            score += _idf(index, term) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * doc["length"] / avg))
        section = _best_section(doc, set(matched))
        hits.append(
            {
                "name": doc["name"],
                "score": round(score, 3),
                "coverage": round(coverage, 3),
                "matched_terms": matched,
                "description": doc["description"],
                "section": section["heading"] if section else None,
                "excerpt": section["text"] if section else None,
            }
        )
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:k]
