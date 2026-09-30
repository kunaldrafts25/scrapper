from benchmark.run_one_link import run


def test_one_link_synthetic_regression_corpus():
    outcome = run()
    assert outcome["cases"] == 9
    assert outcome["passed"] == 9, outcome["per_case"]
