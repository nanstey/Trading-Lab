from trading_lab.agent.codegen_guards import check_source


def test_check_source_allows_bisect_stdlib_import() -> None:
    report = check_source("import bisect\n")
    assert report.ok, report.violations
