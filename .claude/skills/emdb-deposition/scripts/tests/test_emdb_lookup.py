"""Unit tests for emdb_lookup.py. The network is mocked entirely - no live
EMDB calls, no onedep_lib/empiar_depositor involved."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from conftest import load_script_module

emdb_lookup = load_script_module("emdb_lookup")


class _FakeMap:
    format = "CCP4"
    dimensions = {"col": 320, "row": 320, "sec": 320}
    pixel_spacing = {
        "x": {"valueOf_": "1.385"},
        "y": {"valueOf_": "1.385"},
        "z": {"valueOf_": "1.385"},
    }
    contour_list = {"contour": [{"level": 0.02, "primary": True}]}


class _FakeEntry:
    id = "EMD-8000"
    method = "singleParticle"
    resolution = 3.7
    admin = {"title": "RNC-SRP-SR complex", "authors_list": ["Smith J", "Doe A"]}
    sample = {"name": {"valueOf_": "Ribosome"}}
    related_pdb_ids = [{"pdb_id": "5gad"}]
    related_emdb_ids = []
    primary_map = _FakeMap()


def test_exists_false_for_malformed_accession_without_network(capsys):
    # A malformed accession must be answered offline (no EMDB import/call).
    with patch("emdb.client.EMDB") as mock_emdb:
        emdb_lookup.cmd_exists("8000")  # missing EMD- prefix
        mock_emdb.assert_not_called()
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is False
    assert "malformed" in out["reason"]


def test_exists_true(capsys):
    with patch("emdb.client.EMDB") as mock_emdb:
        mock_emdb.return_value.get_entry.return_value = _FakeEntry()
        emdb_lookup.cmd_exists("EMD-8000")
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is True


def test_exists_false_on_404(capsys):
    from emdb.exceptions import EMDBAPIError

    with patch("emdb.client.EMDB") as mock_emdb:
        mock_emdb.return_value.get_entry.side_effect = EMDBAPIError(
            "Entry not found (Status code: 404)"
        )
        emdb_lookup.cmd_exists("EMD-99999999")
    out = json.loads(capsys.readouterr().out)
    assert out["exists"] is False
    assert "not found" in out["reason"]


def test_exists_reraises_real_server_error():
    # A non-404 API error must NOT be reported as "absent" - it propagates
    # (run_cli would turn it into a JSON error at the CLI boundary).
    from emdb.exceptions import EMDBAPIError

    with patch("emdb.client.EMDB") as mock_emdb:
        mock_emdb.return_value.get_entry.side_effect = EMDBAPIError("Connection refused")
        with pytest.raises(EMDBAPIError):
            emdb_lookup.cmd_exists("EMD-8000")


def test_lookup_summarizes_entry(capsys):
    with patch("emdb.client.EMDB") as mock_emdb:
        instance = mock_emdb.return_value
        instance.get_entry.return_value = _FakeEntry()
        instance.get_annotations.return_value = MagicMock(empiar=[MagicMock(id="EMPIAR-10028")])
        emdb_lookup.cmd_lookup("EMD-8000")

    out = json.loads(capsys.readouterr().out)
    assert out["success"] is True
    entry = out["entry"]
    assert entry["id"] == "EMD-8000"
    assert entry["title"] == "RNC-SRP-SR complex"
    assert entry["method"] == "singleParticle"
    assert entry["resolution"] == 3.7
    assert entry["sample_name"] == "Ribosome"
    assert entry["map"]["pixel_spacing"]["x"] == "1.385"
    assert entry["map"]["contour_level"] == 0.02
    assert entry["related_pdb_ids"] == ["5gad"]
    assert entry["related_empiar_ids"] == ["EMPIAR-10028"]


def test_lookup_flattens_nested_author_shape(capsys):
    # Real entries return admin["authors_list"] as {"author": [{"valueOf_": ...}]}
    # rather than a flat list; the summary flattens it to names.
    class _NestedAuthors(_FakeEntry):
        admin = {
            "title": "T",
            "authors_list": {"author": [{"valueOf_": "Jomaa A"}, {"valueOf_": "Boehringer D"}]},
        }

    with patch("emdb.client.EMDB") as mock_emdb:
        instance = mock_emdb.return_value
        instance.get_entry.return_value = _NestedAuthors()
        instance.get_annotations.return_value = MagicMock(empiar=[])
        emdb_lookup.cmd_lookup("EMD-8000")

    out = json.loads(capsys.readouterr().out)
    assert out["entry"]["authors"] == ["Jomaa A", "Boehringer D"]


def test_lookup_rejects_malformed_accession(capsys):
    with pytest.raises(SystemExit) as exc:
        emdb_lookup.cmd_lookup("not-an-accession")
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["success"] is False
    assert "valid EMDB accession" in out["error"]


def test_lookup_summary_survives_sparse_entry(capsys):
    # An entry missing most fields must summarize to Nones, not crash.
    class _Sparse:
        id = "EMD-0001"

    with patch("emdb.client.EMDB") as mock_emdb:
        instance = mock_emdb.return_value
        instance.get_entry.return_value = _Sparse()
        instance.get_annotations.side_effect = Exception("no annotations")
        emdb_lookup.cmd_lookup("EMD-0001")

    out = json.loads(capsys.readouterr().out)
    assert out["entry"]["id"] == "EMD-0001"
    assert out["entry"]["title"] is None
    assert out["entry"]["map"]["pixel_spacing"]["x"] is None
    assert out["entry"]["related_empiar_ids"] == []
