import json

from evals.validate_corpus import validate


def _incident(incident_id: str, cls: str = "ml-api-403-search") -> dict:
    return {
        "incident_id": incident_id,
        "source_system": "photolist-latam",
        "alarm_name": "photolist-x-errors",
        "timestamp": "2026-09-01T00:00:00Z",
        "incident_class": cls,
        "evidence": [{"type": "log", "ref": "q1:line1"}],
        "ground_truth_root_cause": "cause",
        "expected_top_3": ["cause"],
    }


def test_placeholders_do_not_count(tmp_path, capsys):
    (tmp_path / "_placeholder-a.json").write_text(json.dumps(_incident("placeholder-a")))
    (tmp_path / "real-1.json").write_text(json.dumps(_incident("real-1")))
    assert validate(tmp_path) == 0
    out = capsys.readouterr().out
    assert "checked 1 incident(s)" in out
    assert "only 1 incident(s)" in out


def test_only_placeholders_is_an_error(tmp_path):
    (tmp_path / "_placeholder-a.json").write_text(json.dumps(_incident("placeholder-a")))
    assert validate(tmp_path) == 1


def test_schema_error_fails(tmp_path):
    bad = _incident("bad")
    del bad["evidence"]
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    assert validate(tmp_path) == 1


def test_commit_and_memory_evidence_are_valid_but_not_m3_eligible(tmp_path, capsys):
    rec = _incident("mem-1")
    rec["evidence"] = [{"type": "memory", "ref": "operator recollection"}, {"type": "commit", "ref": "abc123"}]
    (tmp_path / "mem-1.json").write_text(json.dumps(rec))
    assert validate(tmp_path) == 0
    assert "not M3/held-out eligible" in capsys.readouterr().out


def test_incident_with_one_retrievable_item_is_eligible(tmp_path, capsys):
    rec = _incident("mixed-1")
    rec["evidence"].append({"type": "commit", "ref": "abc123"})
    (tmp_path / "mixed-1.json").write_text(json.dumps(rec))
    validate(tmp_path)
    assert "not M3/held-out eligible" not in capsys.readouterr().out
