from benchmark import run


def test_versioned_fixture_sites_and_denominators():
    version, cases = run.load_cases("v2")
    assert version == "2.0" and len(cases) >= 16
    development = {case["site"] for case in cases if case["split"] == "development"}
    held_out = {case["site"] for case in cases if case["split"] == "held_out"}
    assert not development & held_out
    report = run.score(cases)
    assert report["summary"]["denominators"]["fields"] == 3 * len(cases)
    assert all(row["fields"] for row in report["per_case"])
    assert report["summary"]["denominators"]["failures"] == 0


def test_invalid_evidence_counts_as_wrong(monkeypatch):
    _, cases = run.load_cases("v2")
    case = next(item for item in cases if item["id"] == "dev-static")
    original = run.LocalAdapter.run
    def forged(self, selected):
        result, captures = original(self, selected)
        result["fields"]["support"]["evidence"][0]["locator"] = "p:nth-of-type(999)"
        return result, captures
    monkeypatch.setattr(run.LocalAdapter, "run", forged)
    report = run.score([case])["summary"]
    assert report["denominators"]["accepted"] == 3
    assert report["denominators"]["incorrect_accepted"] == 1
    assert report["field_precision"] < 1
    assert report["evidence_validity"] < 1
