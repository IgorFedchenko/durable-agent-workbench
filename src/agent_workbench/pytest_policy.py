"""Mandatory-suite outcome policy; an xfail marker can never turn XPASS green.

Loaded automatically from this distribution's pytest11 entry point. Mandatory
commands also explicitly request the plugin, including when autoload is disabled.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # The policy applies to every test in a required suite. An inherited or local
    # strict=False marker must not weaken the repository's strict xfail default.
    for item in items:
        for marker in list(item.iter_markers(name="xfail")):
            item.add_marker(
                pytest.mark.xfail(*marker.args, **{**marker.kwargs, "strict": True}),
                append=False,
            )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    del item, call
    report = yield
    # Retain failure in JUnit even if a marker was introduced after collection.
    if report.passed and hasattr(report, "wasxfail"):
        report.outcome = "failed"
        delattr(report, "wasxfail")
        report.longrepr = "XPASS is forbidden by the mandatory-suite outcome policy"
    return report
