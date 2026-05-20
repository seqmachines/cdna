#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cdna_engine.cli import app


runner = CliRunner()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    result = runner.invoke(app, ["--help"])
    assert_true(result.exit_code == 0, result.output)
    assert_true("cDNA parser and curation engine" in result.output, "expected top-level help")

    result = runner.invoke(app, ["curate", "oligos", "--help"])
    assert_true(result.exit_code == 0, result.output)
    assert_true("--max-iterations" in result.output, "expected curation help")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "protocol.txt"
        input_path.write_text("Primer A: 5'- TTGACCTGACCTGACCTGACCTA -3'\n", encoding="utf-8")
        output_path = tmp_path / "oligos.tsv"
        result = runner.invoke(app, ["extract", "oligos", "--input", str(input_path), "--output", str(output_path)])
        assert_true(result.exit_code == 0, result.output)
        output = output_path.read_text(encoding="utf-8")
        assert_true("TTGACCTGACCTGACCTGACCTA" in output, "expected deterministic oligo output")

        os.environ["CDNA_TEST_LLM_RESPONSE"] = json.dumps(
            {
                "audit_status": "pass",
                "missing_sequences": [],
                "candidate_reviews": [],
                "oligo_terms": [],
                "suspected_regex_gaps": [],
                "proposed_inventory_rows": [],
                "proposed_extractor_changes": [],
                "human_review_required": True,
            }
        )
        run_dir = tmp_path / "curation"
        result = runner.invoke(
            app,
            [
                "curate",
                "oligos",
                "--input",
                str(input_path),
                "--model",
                "test/model",
                "--max-iterations",
                "1",
                "--run-dir",
                str(run_dir),
            ],
            env={"CDNA_TEST_LLM_RESPONSE": os.environ["CDNA_TEST_LLM_RESPONSE"]},
        )
        assert_true(result.exit_code == 0, result.output)
        assert_true((run_dir / "iteration-1.audit.json").exists(), "expected curation audit artifact")
        del os.environ["CDNA_TEST_LLM_RESPONSE"]

    print("cdna cli smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
