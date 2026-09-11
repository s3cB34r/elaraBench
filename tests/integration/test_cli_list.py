"""Public suite discovery requires no provider or writable run directory."""
from pathlib import Path

import pytest

from elarabench.benchmark import load_benchmark_suite
from elarabench.builtin import available_builtin_suites, get_builtin_suite_path
from elarabench.cli import main


def test_list_is_complete_ordered_and_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("discovery must not construct providers or contact the network")

    monkeypatch.setattr("elarabench.cli.create_provider", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert lines[0].split() == ["Suite", "ID", "Version", "Cases"]
    rows = [line.split() for line in lines[1:]]
    assert [row[0] for row in rows] == list(available_builtin_suites())
    assert len(rows) == 10
    assert sum(int(row[2]) for row in rows) == 282
    for suite_id, version, count in rows:
        loaded = load_benchmark_suite(get_builtin_suite_path(suite_id))
        assert version == loaded.suite.version
        assert int(count) == len(loaded.suite.cases)
    assert list(tmp_path.iterdir()) == []
    assert main(["list"]) == 0
    assert capsys.readouterr().out == output.out
