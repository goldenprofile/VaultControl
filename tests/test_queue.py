import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vaultctl import cli, progress, queue, worker
from vaultctl.cli import EXIT_CONFIG_ERROR, EXIT_VAULT_BUSY, main


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Изолированный каталог состояния очереди на каждый тест."""
    directory = tmp_path / "state"
    monkeypatch.setenv("VAULTCTL_STATE_DIR", str(directory))
    return directory


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    path.mkdir()
    return path


@pytest.fixture
def allow_stream(monkeypatch):
    """Позволяет заглушке считать стрим-агентом: воркер зовёт её через python.

    Маркер — валидный флаг интерпретатора (-q): всё с ведущим дефисом python
    съест как свою опцию ещё до имени скрипта.
    """
    monkeypatch.setitem(progress.STREAM_FLAGS_BY_AGENT, "python", ("-q",))


def streaming_agent_script(tmp_path: Path, exit_code: int = 0) -> str:
    """Агент-заглушка: отвечает потоком событий Claude и завершается с кодом."""
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success',\n"
        "    'result': 'готово'}))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return str(script)


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _blocking_agent(tmp_path: Path):
    out = tmp_path / "received.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n",
        encoding="utf-8",
    )
    command = " ".join(f'"{part}"' for part in (sys.executable, script, out))
    return command, out


def test_create_item_allocates_sequential_ids(state, vault):
    first = queue.create_item("раз", str(vault), timeout=None, agent_cmd=None)
    second = queue.create_item("два", str(vault), timeout=None, agent_cmd=None)

    assert (first["id"], second["id"]) == (1, 2)
    assert first["status"] == "queued"
    assert first["task"] == "раз"
    assert first["vault"] == str(vault)


def test_worker_lock_is_exclusive(state):
    queue.acquire_lock("worker")
    with pytest.raises(queue.LockBusy):
        queue.acquire_lock("worker")
    queue.release_lock("worker")
    queue.acquire_lock("worker")  # освобождён — берётся снова


def test_stale_worker_lock_is_taken_over(state, monkeypatch):
    queue.acquire_lock("worker")
    monkeypatch.setattr(queue, "pid_alive", lambda pid: False)  # владелец мёртв

    queue.acquire_lock("worker")  # перехват без LockBusy

    assert queue.lock_info("worker")["pid"] == os.getpid()


def test_reconcile_fails_dead_running(state, vault, monkeypatch):
    item = queue.create_item("задача", str(vault), timeout=None, agent_cmd=None)
    queue.update_item(item["id"], status="running", pid=999_999)
    monkeypatch.setattr(queue, "pid_alive", lambda pid: False)

    fixed = queue.reconcile()

    assert fixed == 1
    assert queue.load_item(item["id"])["status"] == "failed"


def test_worker_runs_queued_task_and_captures_result(
    state, vault, tmp_path, monkeypatch, allow_stream
):
    item = queue.create_item("сделай", str(vault), timeout=None, agent_cmd=None)
    agent = streaming_agent_script(tmp_path)
    queue.update_item(item["id"], agent_cmd=f'"{sys.executable}" "{agent}"')

    code = worker.main([str(queue.runs_dir())])

    done = queue.load_item(item["id"])
    assert code == 0
    assert done["status"] == "done"
    assert done["exit_code"] == 0
    assert done["result"] == "готово"
    assert "готово" in Path(done["log"]).read_text(encoding="utf-8")


def test_worker_marks_failed_agent_as_failed(state, vault, tmp_path):
    item = queue.create_item("упадёт", str(vault), timeout=None, agent_cmd=None)
    agent = streaming_agent_script(tmp_path, exit_code=3)
    queue.update_item(item["id"], agent_cmd=f'"{sys.executable}" "{agent}"')

    worker.main([str(queue.runs_dir())])

    done = queue.load_item(item["id"])
    assert done["status"] == "failed"
    assert done["exit_code"] == 3


def test_worker_drains_queue_sequentially(state, vault, tmp_path):
    agent = streaming_agent_script(tmp_path)
    command = f'"{sys.executable}" "{agent}"'
    queue.create_item("первая", str(vault), timeout=None, agent_cmd=command)
    queue.create_item("вторая", str(vault), timeout=None, agent_cmd=command)

    assert worker.main([str(queue.runs_dir())]) == 0

    statuses = [item["status"] for item in queue.list_items()]
    assert statuses == ["done", "done"]
    # после опустевшей очереди воркер завершился — лок свободен
    assert queue.lock_info("worker") is None


def test_worker_skips_cancelled_tasks(state, vault, tmp_path):
    agent = streaming_agent_script(tmp_path)
    command = f'"{sys.executable}" "{agent}"'
    first = queue.create_item("отменят", str(vault), timeout=None, agent_cmd=command)
    queue.update_item(first["id"], status="cancelled", finished=queue.now_iso())
    second = queue.create_item("выполнится", str(vault), timeout=None, agent_cmd=command)

    worker.main([str(queue.runs_dir())])

    assert queue.load_item(first["id"])["status"] == "cancelled"
    assert queue.load_item(second["id"])["status"] == "done"


def test_worker_releases_vault_lock(state, vault, tmp_path):
    agent = streaming_agent_script(tmp_path)
    item = queue.create_item(
        "задача", str(vault), timeout=None, agent_cmd=f'"{sys.executable}" "{agent}"'
    )

    worker.main([str(queue.runs_dir())])

    assert queue.lock_info(queue.vault_lock_name(str(vault))) is None


def test_block_run_refuses_when_vault_busy(state, vault, tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setattr(sys, "stdin", _FakeTty())
    queue.acquire_lock(queue.vault_lock_name(str(vault)))

    code = main(["--quiet", "задача"])

    assert code == EXIT_VAULT_BUSY


def test_block_run_takes_vault_lock(state, vault, tmp_path, monkeypatch):
    command, out = _blocking_agent(tmp_path)
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", command)
    monkeypatch.setattr(sys, "stdin", _FakeTty())

    assert main(["--quiet", "задача"]) == 0
    assert out.read_text(encoding="utf-8") == "задача"
    assert queue.lock_info(queue.vault_lock_name(str(vault))) is None


def test_detach_queues_task_without_running(state, vault, tmp_path, monkeypatch):
    command, out = _blocking_agent(tmp_path)
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", command)
    monkeypatch.setattr(sys, "stdin", _FakeTty())
    spawned = []
    monkeypatch.setattr(queue, "spawn_worker", lambda: spawned.append(1) or True)

    code = main(["-d", "--quiet", "фоновая задача"])

    assert code == 0
    assert not out.exists()  # агент не запускался — задача только в очереди
    items = queue.list_items()
    assert len(items) == 1
    assert items[0]["status"] == "queued"
    assert items[0]["task"] == "фоновая задача"
    assert spawned == [1]


def test_detach_second_submission_does_not_spawn_worker(
    state, vault, tmp_path, monkeypatch
):
    command, _ = _blocking_agent(tmp_path)
    monkeypatch.setenv("VAULTCTL_AGENT_CMD", command)
    monkeypatch.setattr(sys, "stdin", _FakeTty())
    monkeypatch.setattr(queue, "worker_busy", lambda: True)

    main(["-d", "--quiet", "первая"])
    code = main(["-d", "--quiet", "вторая"])

    assert code == 0
    assert len(queue.list_items()) == 2


def test_q_list_shows_statuses(state, vault, capsys):
    done = queue.create_item("готовая", str(vault), timeout=None, agent_cmd=None)
    queue.update_item(done["id"], status="done", finished=queue.now_iso())
    queued = queue.create_item("ждёт", str(vault), timeout=None, agent_cmd=None)

    main(["q"])

    out = capsys.readouterr().out
    assert f"[q#{done['id']}] done" in out
    assert f"[q#{queued['id']}] queued" in out
    assert "готовая" in out


def test_q_cancel_queued_task(state, vault, capsys):
    item = queue.create_item("позже", str(vault), timeout=None, agent_cmd=None)

    code = main(["q", "cancel", str(item["id"])])

    assert code == 0
    assert queue.load_item(item["id"])["status"] == "cancelled"


def test_q_cancel_kills_running_task(state, vault, capsys, monkeypatch):
    item = queue.create_item("долгая", str(vault), timeout=None, agent_cmd=None)
    queue.update_item(item["id"], status="running", pid=os.getpid())
    killed = []
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kw: killed.append(cmd)
        or subprocess.CompletedProcess(cmd, 0),
    )
    monkeypatch.setattr(queue, "spawn_worker", lambda: False)

    code = main(["q", "cancel", str(item["id"])])

    assert code == 0
    assert queue.load_item(item["id"])["status"] == "cancelled"
    assert killed and killed[0][:4] == ["taskkill", "/PID", str(os.getpid()), "/T"]


def test_q_result_prints_final_text(state, vault, capsys):
    item = queue.create_item("с ответом", str(vault), timeout=None, agent_cmd=None)
    queue.update_item(item["id"], status="done", result="Заметка сохранена")

    main(["q", "result", str(item["id"])])

    assert "Заметка сохранена" in capsys.readouterr().out


def test_q_result_for_running_task(state, vault, capsys):
    item = queue.create_item("работает", str(vault), timeout=None, agent_cmd=None)
    queue.update_item(item["id"], status="running", pid=os.getpid())

    main(["q", "result", str(item["id"])])

    assert "running" in capsys.readouterr().out


def test_q_unknown_id_is_config_error(state, capsys):
    assert main(["q", "result", "999"]) == EXIT_CONFIG_ERROR
    assert main(["q", "cancel", "999"]) == EXIT_CONFIG_ERROR


def test_q_list_survives_emoji_on_cp1251_console(state, vault, monkeypatch):
    class Cp1251(io.StringIO):
        encoding = "cp1251"

    out = Cp1251()
    monkeypatch.setattr(sys, "stdout", out)
    queue.create_item("задача 🚀", str(vault), timeout=None, agent_cmd=None)

    cli._queue_list()

    assert "задача ?" in out.getvalue()
