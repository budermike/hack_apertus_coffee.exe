"""Unit tests for present/evaluation.py and GET /api/evaluation (#71)."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock
from urllib.request import urlopen

from hack_apertus_coffee_exe.present.evaluation import evaluation_response, load_evaluation
from hack_apertus_coffee_exe.summary.api import make_server


def test_load_evaluation_reads_csv_calibration_and_models(tmp_path: Path) -> None:
    tables_dir = tmp_path / "tables"
    tables_dir.mkdir()
    (tables_dir / "gesamtvergleich_relevanz.csv").write_text(
        "Stufe 1,Stufe 2,F2\nEmbedding,Apertus 70B,0.7357\n",
        encoding="utf-8",
    )
    (tables_dir / "relevance_calibration_q2_2026.json").write_text(
        json.dumps({"n": 300, "n_pos": 59, "t": 0.1484, "periods": ["2025-Q4", "2026-Q1"]}),
        encoding="utf-8",
    )

    models_md = tmp_path / "MODELS.md"
    models_md.write_text(
        """# Register

## 1. Laufzeit (in der abgegebenen Lösung)

| # | Modell | Version / ID | Quelle | Rolle | Open-Weights | Lizenz |
|---|---|---|---|---|---|---|
| R1 | Apertus v1.5 (8B) | `apertus-v1-5-8b` | Gateway | Triage | ja | Apache-2.0 |

## 2. Entwicklung (nur beim Bauen verwendet)

| # | Tool / Modell | Version | Anbieter | Rolle |
|---|---|---|---|---|
| D3 | Antigravity | Gemini | Google DeepMind | Coding |
| D4 | <Video-Tool> | | | Video |
""",
        encoding="utf-8",
    )

    data = load_evaluation(tables_dir, models_md)
    assert data["available"] is True
    assert len(data["tables"]) == 1
    tbl = data["tables"][0]
    assert tbl["filename"] == "gesamtvergleich_relevanz.csv"
    assert tbl["columns"] == ["Stufe 1", "Stufe 2", "F2"]
    assert tbl["rows"] == [["Embedding", "Apertus 70B", "0.7357"]]
    assert tbl["totalRows"] == 1
    assert data["calibration"] == {"n": 300, "n_pos": 59, "t": 0.1484, "periods": ["2025-Q4", "2026-Q1"]}

    runtime_rows = data["models"]["runtime"]["rows"]
    assert len(runtime_rows) == 1
    assert runtime_rows[0][1] == "Apertus v1.5 (8B)"
    assert runtime_rows[0][2] == "apertus-v1-5-8b"

    dev_rows = data["models"]["development"]["rows"]
    assert len(dev_rows) == 1
    assert dev_rows[0][0] == "D3"


def test_load_evaluation_handles_missing_files(tmp_path: Path) -> None:
    status, data = evaluation_response(tmp_path / "missing_tables", tmp_path / "missing_models.md")
    assert status == HTTPStatus.OK
    assert data["available"] is False
    assert data["tables"] == []
    assert data["calibration"] is None
    assert data["models"]["runtime"]["rows"] == []
    assert data["models"]["development"]["rows"] == []


def test_http_get_evaluation_endpoint(tmp_path: Path) -> None:
    tables_dir = tmp_path / "tables"
    tables_dir.mkdir()
    (tables_dir / "crawl_report.csv").write_text("month,total\n2026-01,609\n", encoding="utf-8")
    models_md = tmp_path / "MODELS.md"
    models_md.write_text(
        "## 1. Laufzeit\n| # | Modell |\n|---|---|\n| R1 | Apertus |\n",
        encoding="utf-8",
    )
    static_dir = tmp_path / "static"
    static_dir.mkdir()

    server = make_server(
        MagicMock(),
        "127.0.0.1",
        0,
        static_dir,
        tables_dir=tables_dir,
        models_path=models_md,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/evaluation", timeout=5) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["available"] is True
        assert payload["tables"][0]["rows"] == [["2026-01", "609"]]
        assert payload["models"]["runtime"]["rows"] == [["R1", "Apertus"]]
    finally:
        server.shutdown()
        server.server_close()
