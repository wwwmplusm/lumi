import json
import os
import stat
import urllib.error

from engine import onboarding, sources
from lumi.cli import main


def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for key in onboarding.KEYS:
        monkeypatch.delenv(key, raising=False)


def test_save_and_load_env_precedence(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    onboarding.save_credentials({"OPENALEX_API_KEY": "stored", "S2_API_KEY": "s2-secret"})
    assert stat.S_IMODE(onboarding.config_path().stat().st_mode) == 0o600
    monkeypatch.setenv("OPENALEX_API_KEY", "environment")
    onboarding.load_credentials()
    assert os.environ["OPENALEX_API_KEY"] == "environment"
    assert os.environ["S2_API_KEY"] == "s2-secret"


def test_setup_import_and_doctor_without_project(tmp_path, monkeypatch, capsys):
    isolate(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORE_API_KEY", "private-token")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "reader@example.org")
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: "/bin/" + name)
    assert main(["setup", "--non-interactive"]) == 0
    assert onboarding.read_credentials()["CORE_API_KEY"] == "private-token"
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "private-token" not in output and "reader@example.org" not in output


def test_interactive_keep_delete_replace(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    onboarding.save_credentials({"OPENALEX_API_KEY": "old", "S2_API_KEY": "remove"})
    monkeypatch.setattr(onboarding.sys.stdin, "isatty", lambda: True)
    answers = iter(["", "-", "a@example.org", "new-core", "", ""])
    monkeypatch.setattr(onboarding.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr(onboarding, "doctor", lambda online: 0)
    assert main(["setup"]) == 0
    assert onboarding.read_credentials() == {"OPENALEX_API_KEY": "old", "UNPAYWALL_EMAIL": "a@example.org", "CORE_API_KEY": "new-core"}


def test_bad_config_does_not_echo_contents(tmp_path, monkeypatch, capsys):
    isolate(tmp_path, monkeypatch)
    onboarding.config_path().parent.mkdir()
    onboarding.config_path().write_text('{"OPENALEX_API_KEY": "super-secret"')
    assert main(["doctor"]) == 2
    assert "super-secret" not in capsys.readouterr().err


def test_redaction_and_online_failure(tmp_path, monkeypatch, capsys):
    isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("S2_API_KEY", "secret/+?")
    assert onboarding.redact("secret/+? secret%2F%2B%3F") == "[скрыто] [скрыто]"
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://test/?key=secret/+?", 401, "secret/+?", {}, None)
    monkeypatch.setattr(sources, "semantic_scholar", fail)
    monkeypatch.setattr(sources, "arxiv", lambda *args: [1])
    monkeypatch.setattr(sources, "crossref", lambda *args: [1])
    monkeypatch.setattr(sources, "get_json", lambda *args: {"data": [1]})
    assert onboarding.probe_apis() == 1
    output = capsys.readouterr().out
    assert "HTTP 401" in output and "secret" not in output


def test_save_failure_preserves_previous(tmp_path, monkeypatch):
    import pytest
    isolate(tmp_path, monkeypatch)
    onboarding.save_credentials({"S2_API_KEY": "old"})
    def fail(*args):
        raise OSError("failed replace")
    monkeypatch.setattr(onboarding.os, "replace", fail)
    with pytest.raises(OSError):
        onboarding.save_credentials({"S2_API_KEY": "new"})
    assert onboarding.read_credentials() == {"S2_API_KEY": "old"}
    assert len(list(onboarding.config_path().parent.iterdir())) == 1


def test_retired_provider_credentials_are_preserved_not_loaded(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    monkeypatch.delenv('PATENTSVIEW_API_KEY', raising=False)
    onboarding.save_credentials({'PATENTSVIEW_API_KEY': 'keep-old-secret', 'S2_API_KEY': 'current'})
    onboarding.load_credentials()
    assert 'PATENTSVIEW_API_KEY' not in os.environ
    assert os.environ['S2_API_KEY'] == 'current'
    monkeypatch.setattr(onboarding, 'doctor', lambda online: 0)
    assert main(['setup', '--non-interactive']) == 0
    assert onboarding.read_credentials()['PATENTSVIEW_API_KEY'] == 'keep-old-secret'
