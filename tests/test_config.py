import os

import pytest

from vaultctl.config import VaultNotFoundError, load_env, resolve_vault_path


def test_resolve_vault_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    assert resolve_vault_path() == tmp_path.resolve()


def test_resolve_vault_path_env_points_to_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "missing"))
    with pytest.raises(VaultNotFoundError):
        resolve_vault_path()


def test_resolve_vault_path_autodetect(tmp_path, monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    (tmp_path / ".obsidian").mkdir()
    nested = tmp_path / "projects" / "sub"
    nested.mkdir(parents=True)
    assert resolve_vault_path(nested) == tmp_path.resolve()


def test_resolve_vault_path_not_found(tmp_path, monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    with pytest.raises(VaultNotFoundError):
        resolve_vault_path(tmp_path)


def test_load_env_finds_dotenv_upwards(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OBSIDIAN_VAULT_PATH=/vault\n", encoding="utf-8")
    nested = tmp_path / "projects" / "sub"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)

    assert load_env() == tmp_path / ".env"
    assert os.environ["OBSIDIAN_VAULT_PATH"] == "/vault"


def test_load_env_does_not_override_existing(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OBSIDIAN_VAULT_PATH=/from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "/from-shell")

    load_env()

    assert os.environ["OBSIDIAN_VAULT_PATH"] == "/from-shell"


def test_load_env_explicit_path(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    env_file.write_text("VAULTCTL_AGENT_CMD=codex exec\n", encoding="utf-8")
    monkeypatch.delenv("VAULTCTL_AGENT_CMD", raising=False)

    assert load_env(env_file) == env_file
    assert os.environ["VAULTCTL_AGENT_CMD"] == "codex exec"


def test_load_env_explicit_path_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_env(tmp_path / "absent.env")
