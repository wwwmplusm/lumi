"""A broken cross-library search must not look like an absent quotation."""
import sqlite3

import pytest

from engine import index, quotes
from lumi.cli import main


def test_check_reports_index_error_instead_of_absence(project, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise sqlite3.DatabaseError("index unavailable")
    monkeypatch.setattr(index, "search_pages", fail)
    quote = "Completely unrelated quotation with several searchable words."
    assert main(["--project", str(project.root), "check", "W1", quote]) == 2
    output = capsys.readouterr()
    assert "index unavailable" in output.err
    assert "ABSENT" not in output.out


def test_unexpected_search_bug_is_not_swallowed(project, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("unexpected bug")
    monkeypatch.setattr(index, "search_pages", fail)
    with pytest.raises(RuntimeError, match="unexpected bug"):
        quotes.find_elsewhere(project, "W1", "Several searchable words in this quotation")
