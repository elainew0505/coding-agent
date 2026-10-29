from importlib.metadata import version

from typer.testing import CliRunner

from coding_agent.cli import app

runner = CliRunner()


def test_version_flag_prints_package_version_and_exits_zero():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"coding-agent {version('coding-agent')}"
