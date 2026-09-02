import io
import sys

import pytest

from vaultctl import runner
from vaultctl.cli import EXIT_CONFIG_ERROR, main

POST = "Нейродайджест (#128)\n\n- GLM 5.3 Flash — $0,15/$0,5 за миллион токенов."


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Готовый vault и агент-заглушка, записывающая полученную задачу в файл."""
    path = tmp_path / "vault"
    path.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(path))
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)
    monkeypatch.delenv("VAULTCTL_AGENT_INPUT", raising=False)
    monkeypatch.delenv("VAULTCTL_STREAM", raising=False)
    return path


@pytest.fixture
def agent(tmp_path):
    """Команда агента-заглушки и файл, куда он кладёт полученную задачу."""
    out = tmp_path / "received.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        "import sys, pathlib\n"
        "task = (\n"
        "    sys.argv[2] if len(sys.argv) > 2\n"
        "    else sys.stdin.buffer.read().decode('utf-8')\n"
        ")\n"
        "pathlib.Path(sys.argv[1]).write_text(task, encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = " ".join(f'"{part}"' for part in (sys.executable, script, out))
    return command, out


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_main_passes_positional_task(vault, agent, monkeypatch):
    command, out = agent
    monkeypatch.setattr(sys, "stdin", FakeTty())

    code = main(["--quiet", "--agent-cmd", command, "сохрани", "ссылку"])

    assert code == 0
    assert out.read_text(encoding="utf-8") == "сохрани ссылку"


def test_main_reads_task_from_file(vault, agent, tmp_path, monkeypatch):
    command, out = agent
    source = tmp_path / "post.txt"
    source.write_text(POST, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", FakeTty())

    code = main(["--quiet", "--agent-cmd", command, "--file", str(source), "clip:"])

    assert code == 0
    assert out.read_text(encoding="utf-8") == f"clip:\n\n{POST}"


def test_main_reads_task_from_pipe(vault, agent, monkeypatch):
    command, out = agent
    monkeypatch.setattr(sys, "stdin", io.StringIO(POST))

    code = main(["--quiet", "--agent-cmd", command, "clip:"])

    assert code == 0
    assert out.read_text(encoding="utf-8") == f"clip:\n\n{POST}"


def test_main_sends_long_task_through_stdin(vault, agent, monkeypatch):
    command, out = agent
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * (runner.arg_limit() + 1)))

    code = main(["--quiet", "--agent-cmd", command, "clip:"])

    assert code == 0
    assert out.read_text(encoding="utf-8").endswith("x" * 100)


def test_main_reports_empty_task(vault, agent, monkeypatch):
    command, _ = agent
    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    code = main(["--quiet", "--agent-cmd", command])

    assert code == EXIT_CONFIG_ERROR
    assert "пуста" in stderr.getvalue()


def test_main_rejects_conflicting_sources(vault, agent, tmp_path, monkeypatch):
    command, _ = agent
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "stdin", FakeTty())

    code = main(
        ["--quiet", "--agent-cmd", command, "--clipboard", "--file", str(tmp_path)]
    )

    assert code == EXIT_CONFIG_ERROR
    assert "взаимоисключающие" in stderr.getvalue()


def test_main_honours_agent_input_from_env(vault, agent, monkeypatch):
    command, out = agent
    monkeypatch.setenv("VAULTCTL_AGENT_INPUT", "stdin")
    monkeypatch.setattr(sys, "stdin", FakeTty())

    code = main(["--quiet", "--agent-cmd", command, "задача"])

    assert code == 0
    assert out.read_text(encoding="utf-8") == "задача"


def test_main_reports_unknown_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "нет"))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    assert main(["--quiet", "задача"]) == EXIT_CONFIG_ERROR
    assert "несуществующую" in stderr.getvalue()
