import pytest

from vaultctl.config import VaultNotFoundError, resolve_vault_path


def test_resolve_vault_path_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    assert resolve_vault_path() == tmp_path.resolve()


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
