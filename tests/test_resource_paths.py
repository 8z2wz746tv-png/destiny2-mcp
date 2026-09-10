from __future__ import annotations

from pathlib import Path

from destiny_mcp import config


def test_resolve_resource_dir_prefers_project_checkout(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "checkout"
    prompt_dir = project_root / "prompts"
    prompt_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", project_root)

    assert config.resolve_resource_dir("prompts") == prompt_dir


def test_resolve_resource_dir_falls_back_to_installed_share(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "missing-checkout"
    install_prefix = tmp_path / "venv"
    skill_dir = install_prefix / "share" / "destiny-mcp" / "skills"
    skill_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(config.sys, "prefix", str(install_prefix))

    assert config.resolve_resource_dir("skills") == skill_dir


def test_community_data_resolver_ignores_unrelated_share_directory(
    monkeypatch, tmp_path: Path
) -> None:
    unrelated = tmp_path / "share"
    unrelated.mkdir()
    (unrelated / "README.md").write_text("not community data", encoding="utf-8")
    installed = tmp_path / "prefix" / "share" / "destiny-mcp" / "community"
    installed.mkdir(parents=True)
    (installed / "weapon-perks.md").write_text("# Perks", encoding="utf-8")
    (installed / "armor-sets.md").write_text("# Sets", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config.sys, "prefix", str(tmp_path / "prefix"))

    assert config.resolve_community_data_dir() == installed
