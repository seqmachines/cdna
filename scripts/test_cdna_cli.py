#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cdna_engine.cli import app
from cdna_engine.io import xlsx_to_text
from cdna_engine.oligos import codex_update
from cdna_engine.oligos.codex_update import (
    build_codex_prompt,
    master_inventory_script_path,
    unique_candidate_names,
    validate_generated_script,
)
from cdna_engine.oligos.extract import _extract_with_temporary_patch
from cdna_engine.oligos.inventory import extract_sequence_inventory


runner = CliRunner()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_xlsx(path: Path, rows: list[list[str]]) -> None:
    shared_values: list[str] = []
    shared_index: dict[str, int] = {}
    row_xml: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_number, value in enumerate(row, start=1):
            column = chr(ord("A") + col_number - 1)
            if value not in shared_index:
                shared_index[value] = len(shared_values)
                shared_values.append(value)
            cells.append(
                f'<c r="{column}{row_number}" t="s"><v>{shared_index[value]}</v></c>'
            )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared_values)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
</Types>""")
        archive.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Oligos" sheetId="1" r:id="rId1"/></sheets>
</workbook>""")
        archive.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""")
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{"".join(row_xml)}</sheetData>
</worksheet>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_values)}" uniqueCount="{len(shared_values)}">{shared_xml}</sst>""",
        )


def main() -> int:
    result = runner.invoke(app, ["--help"])
    assert_true(result.exit_code == 0, result.output)
    assert_true("cDNA parser and curation engine" in result.output, "expected top-level help")

    result = runner.invoke(app, ["curate", "oligos", "--help"])
    assert_true(result.exit_code == 0, result.output)
    assert_true("--max-iterations" in result.output, "expected curation help")
    assert_true("--interactive" not in result.output, "customer extraction should not expose interactive review")
    assert_true("promote" in result.output, "expected owner/developer promote command")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "protocol.txt"
        input_path.write_text("Primer A: 5'- TTGACCTGACCTGACCTGACCTA -3'\n", encoding="utf-8")
        xlsx_path = tmp_path / "SPLiT-seq.xlsx"
        write_xlsx(
            xlsx_path,
            [["name", "sequence"], ["Test Primer", "5'- AACCGGTTAACCGGTTAACC -3'"]],
        )
        assert_true(
            "AACCGGTTAACCGGTTAACC" in xlsx_to_text(xlsx_path),
            "expected XLSX text extraction to include oligo sequence",
        )
        output_path = tmp_path / "oligos.tsv"
        result = runner.invoke(
            app,
            [
                "extract",
                "oligos",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--deterministic-only",
            ],
        )
        assert_true(result.exit_code == 0, result.output)
        output = output_path.read_text(encoding="utf-8")
        assert_true("TTGACCTGACCTGACCTGACCTA" in output, "expected deterministic oligo output")

        audit_pass = json.dumps(
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
        os.environ["CDNA_TEST_LLM_RESPONSE"] = audit_pass
        final_output_path = tmp_path / "final-oligos.tsv"
        result = runner.invoke(
            app,
            [
                "extract",
                "oligos",
                "--input",
                str(input_path),
                "--output",
                str(final_output_path),
                "--model",
                "test/model",
            ],
            env={"CDNA_TEST_LLM_RESPONSE": audit_pass},
        )
        assert_true(result.exit_code == 0, result.output)
        assert_true((tmp_path / "final-oligos.extract.json").exists(), "expected final extraction JSON")
        assert_true("TTGACCTGACCTGACCTGACCTA" in final_output_path.read_text(encoding="utf-8"), "expected final oligo output")

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
            env={"CDNA_TEST_LLM_RESPONSE": audit_pass},
        )
        assert_true(result.exit_code == 0, result.output)
        assert_true((run_dir / "iteration-1.audit.json").exists(), "expected curation audit artifact")
        del os.environ["CDNA_TEST_LLM_RESPONSE"]

        probe_text = "Probe X: ACGTACGT\n"
        assert_true(
            not extract_sequence_inventory(probe_text)["candidates"],
            "probe fixture should not be extracted before temporary patch",
        )
        patch = """diff --git a/cdna_engine/oligos/sequence_inventory.py b/cdna_engine/oligos/sequence_inventory.py
--- a/cdna_engine/oligos/sequence_inventory.py
+++ b/cdna_engine/oligos/sequence_inventory.py
@@ -82,6 +82,7 @@ LABEL_TERMS = [
     "seq b",
     "seqb",
     "sequence",
     "primer type",
+    "probe",
     "wellposition",
     "well position",
"""
        patched_inventory, patch_error = _extract_with_temporary_patch(probe_text, patch)
        assert_true(patch_error is None, f"temporary patch failed: {patch_error}")
        assert_true(
            any(candidate["sequence"] == "ACGTACGT" for candidate in (patched_inventory or {}).get("candidates", [])),
            "expected temporary patched extractor to find probe sequence",
        )
        assert_true(
            not extract_sequence_inventory(probe_text)["candidates"],
            "canonical extractor should remain unchanged after temporary patch",
        )

        candidate_json = tmp_path / "candidates.json"
        candidate_json.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "canonical_id": "split-seq_test-primer",
                            "display_name": "split-seq Test Primer",
                            "fetch_id": "test-primer",
                            "fetch_name": "Test Primer",
                            "record_type": "oligo",
                            "protocol_version": None,
                            "aliases": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        benchmark_json = tmp_path / "benchmark.json"
        result = runner.invoke(
            app,
            [
                "benchmark",
                "deterministic-oligos",
                "--protocol-slug",
                "split-seq",
                "--candidate-json",
                str(candidate_json),
                "--input",
                str(input_path),
                "--input",
                str(xlsx_path),
                "--output",
                str(benchmark_json),
                "--artifacts-dir",
                str(tmp_path / "benchmark-artifacts"),
                "--deterministic-only",
            ],
        )
        assert_true(result.exit_code == 0, result.output)
        benchmark = json.loads(benchmark_json.read_text(encoding="utf-8"))
        assert_true(
            any(
                row.get("canonical_id") == "split-seq_test-primer"
                and row.get("sequence") == "AACCGGTTAACCGGTTAACC"
                for row in benchmark["protocol"]["adapter_primer_sequences"]
            ),
            "expected name-guided deterministic benchmark to find XLSX sequence",
        )

        v1_json = tmp_path / "v1.json"
        result = runner.invoke(
            app,
            [
                "extract",
                "oligos",
                "--protocol-slug",
                "split-seq",
                "--candidate-json",
                str(candidate_json),
                "--input",
                str(input_path),
                "--input",
                str(xlsx_path),
                "--benchmark-json-output",
                str(v1_json),
                "--artifacts-dir",
                str(tmp_path / "v1-artifacts"),
            ],
        )
        assert_true(result.exit_code == 0, result.output)
        v1 = json.loads(v1_json.read_text(encoding="utf-8"))
        assert_true(v1["mode"] == "name_guided_deterministic", "expected deterministic v1 mode label")
        assert_true(v1["audit"]["audit_status"] == "not_run", "expected v1 candidate benchmark to skip LLM audit")
        assert_true(
            any(
                row.get("canonical_id") == "split-seq_test-primer"
                and row.get("sequence") == "AACCGGTTAACCGGTTAACC"
                for row in v1["protocol"]["adapter_primer_sequences"]
            ),
            "expected v1 candidate benchmark to find XLSX sequence without LLM audit",
        )
        assert_true(
            unique_candidate_names(json.loads(candidate_json.read_text(encoding="utf-8"))["candidates"])
            == ["Test Primer"],
            "expected Codex prompt candidates to be unique names only",
        )
        prompt = build_codex_prompt(
            protocol_slug="split-seq",
            inputs=[input_path, xlsx_path],
            candidates=json.loads(candidate_json.read_text(encoding="utf-8"))["candidates"],
            repo_root=Path(__file__).resolve().parents[1],
        )
        name_block = prompt.split("Unique possible adapter/primer/oligo names:", 1)[1].split("Target protocol text", 1)[0]
        assert_true("Test Primer" in name_block, "expected prompt name block to include display name")
        assert_true("record_type" not in name_block, "prompt name block should not include candidate JSON objects")
        assert_true("protocol_version" not in name_block, "prompt name block should not include candidate metadata")

        codex_out = tmp_path / "codex-dry-run"
        result = runner.invoke(
            app,
            [
                "extract",
                "oligos",
                "--protocol-slug",
                "split-seq",
                "--candidate-json",
                str(candidate_json),
                "--input",
                str(input_path),
                "--input",
                str(xlsx_path),
                "--benchmark-json-output",
                str(tmp_path / "v1-codex.json"),
                "--artifacts-dir",
                str(tmp_path / "v1-codex-artifacts"),
                "--use-codex-update",
                "--codex-out",
                str(codex_out),
                "--codex-dry-run",
                "--codex-model",
                "gpt-5.5",
                "--codex-reasoning-effort",
                "xhigh",
            ],
        )
        assert_true(result.exit_code == 0, result.output)
        codex_result = json.loads((codex_out / "result.json").read_text(encoding="utf-8"))
        assert_true(codex_result["status"] == "prompt_written", "expected Codex dry-run prompt artifact")
        assert_true(codex_result["candidate_name_count"] == 1, "expected one unique candidate name")
        assert_true(codex_result["codex_model"] == "gpt-5.5", "expected dry-run Codex model metadata")
        assert_true(codex_result["codex_reasoning_effort"] == "xhigh", "expected dry-run Codex reasoning metadata")
        assert_true(not (codex_out / "sequence_inventory.master.py").exists(), "master script should not be copied into run artifacts")
        assert_true("master_script" not in codex_result, "result metadata should not point to a master script copy")
        assert_true("best.patch" not in codex_result, "Codex flow should no longer use patch artifacts")
        master_script = master_inventory_script_path(Path(__file__).resolve().parents[1]).read_text(encoding="utf-8")
        assert_true(validate_generated_script(master_script)["ok"], "expected master inventory script to validate")
        unsafe_script = master_script + '\nHARDCODED = "AACCGGTTAACCGGTT"\n'
        assert_true(
            not validate_generated_script(unsafe_script)["ok"],
            "expected generated script validation to reject hardcoded DNA literals",
        )

        original_subprocess_run = codex_update.subprocess.run
        original_cwd = Path.cwd()
        calls: list[tuple[list[str], Path, bool]] = []

        def fake_subprocess_run(command: list[str], *, check: bool, cwd: Path, **kwargs) -> subprocess.CompletedProcess:
            calls.append((command, cwd, check))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        try:
            os.chdir(tmp_path)
            relative_codex_out = Path("relative-codex-out")
            relative_codex_out.mkdir()
            relative_prompt = relative_codex_out / "prompt.md"
            relative_response = relative_codex_out / "codex_response.raw.txt"
            relative_prompt.write_text("Return NO_PATCH.\n", encoding="utf-8")
            codex_update.subprocess.run = fake_subprocess_run
            codex_update._run_codex_sdk(
                relative_prompt,
                relative_response,
                relative_codex_out,
                Path(__file__).resolve().parents[1],
            )
        finally:
            codex_update.subprocess.run = original_subprocess_run
            os.chdir(original_cwd)

        assert_true(len(calls) == 1, "expected Codex SDK runner to invoke node once")
        command, cwd, check = calls[0]
        assert_true(check is False, "expected Codex SDK runner to handle node exit status")
        assert_true(Path(command[1]).is_absolute(), "expected node script path to be absolute")
        assert_true(Path(command[2]).is_absolute(), "expected prompt path to be absolute")
        assert_true(Path(command[3]).is_absolute(), "expected raw response path to be absolute")
        assert_true(Path(command[4]).is_absolute(), "expected SDK import path to be absolute")
        assert_true(command[5] == "", "expected empty Codex model arg when model is default")
        assert_true(command[6] == "", "expected empty Codex reasoning arg when effort is default")
        assert_true(Path(cwd).is_absolute(), "expected Codex runner cwd to be absolute")

        original_run_codex_sdk = codex_update._run_codex_sdk
        generated_script = master_script.replace(
            '    "capture",\n',
            '    "capture",\n    "ampliconx",\n',
        )

        def fake_run_codex_sdk(
            prompt_path: Path,
            response_path: Path,
            out_dir: Path,
            repo_root: Path,
            *,
            codex_model: str | None = None,
            codex_reasoning_effort: str | None = None,
        ) -> None:
            response_path.write_text(generated_script, encoding="utf-8")

        try:
            codex_update._run_codex_sdk = fake_run_codex_sdk
            amp_input = tmp_path / "amplicon.txt"
            amp_input.write_text("AmpliconX: ACGTACGT\n", encoding="utf-8")
            amp_candidate_json = tmp_path / "amplicon-candidates.json"
            amp_candidate_json.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "canonical_id": "amplicon-x",
                                "display_name": "AmpliconX",
                                "fetch_id": "amplicon-x",
                                "fetch_name": "AmpliconX",
                                "record_type": "oligo",
                                "protocol_version": None,
                                "aliases": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            amp_out = tmp_path / "amplicon-run"
            result = runner.invoke(
                app,
                [
                    "extract",
                    "oligos",
                    "--protocol-slug",
                    "amplicon-test",
                    "--candidate-json",
                    str(amp_candidate_json),
                    "--input",
                    str(amp_input),
                    "--benchmark-json-output",
                    str(amp_out / "v1.json"),
                    "--artifacts-dir",
                    str(amp_out),
                    "--use-codex-update",
                    "--codex-out",
                    str(amp_out),
                ],
            )
        finally:
            codex_update._run_codex_sdk = original_run_codex_sdk

        assert_true(result.exit_code == 0, result.output)
        amp_result = json.loads((amp_out / "result.json").read_text(encoding="utf-8"))
        assert_true(amp_result["status"] == "generated_script_executed", "expected generated script execution")
        assert_true((amp_out / "sequence_inventory.generated.py").exists(), "expected generated script artifact")
        amp_v1 = json.loads((amp_out / "v1.json").read_text(encoding="utf-8"))
        assert_true(
            any(
                row.get("canonical_id") == "amplicon-x"
                and row.get("sequence") == "ACGTACGT"
                for row in amp_v1["protocol"]["adapter_primer_sequences"]
            ),
            "expected run-local generated script to control inventory extraction",
        )

        open_json = tmp_path / "open-v1.json"
        result = runner.invoke(
            app,
            [
                "extract",
                "oligos",
                "--protocol-slug",
                "open-test",
                "--input",
                str(input_path),
                "--benchmark-json-output",
                str(open_json),
                "--artifacts-dir",
                str(tmp_path / "open-v1-artifacts"),
            ],
        )
        assert_true(result.exit_code == 0, result.output)
        open_v1 = json.loads(open_json.read_text(encoding="utf-8"))
        assert_true(
            any(
                row.get("canonical_id") == "primer-a"
                and row.get("record_type") == "primer"
                and row.get("sequence") == "TTGACCTGACCTGACCTGACCTA"
                for row in open_v1["protocol"]["adapter_primer_sequences"]
            ),
            "expected open v1 extraction to derive IDs from inventory labels without candidate JSON",
        )

        baseline_json = tmp_path / "baseline.json"
        baseline_response = json.dumps(
            {
                "records": [
                    {
                        "canonical_id": "split-seq_test-primer",
                        "display_name": "split-seq Test Primer",
                        "record_type": "oligo",
                        "protocol_version": None,
                        "evidence": "5'- AACCGGTTAACCGGTTAACC -3'",
                        "sequence": "AACCGGTTAACCGGTTAACC",
                        "orientation": "5_to_3",
                    }
                ]
            }
        )
        result = runner.invoke(
            app,
            [
                "benchmark",
                "baseline-oligo",
                "--protocol-slug",
                "split-seq",
                "--candidate-json",
                str(candidate_json),
                "--input",
                str(input_path),
                "--input",
                str(xlsx_path),
                "--output",
                str(baseline_json),
                "--artifacts-dir",
                str(tmp_path / "baseline-artifacts"),
                "--model",
                "test/model",
            ],
            env={"CDNA_TEST_LLM_RESPONSE": baseline_response},
        )
        assert_true(result.exit_code == 0, result.output)
        baseline = json.loads(baseline_json.read_text(encoding="utf-8"))
        assert_true(
            baseline["protocol"]["adapter_primer_sequences"][0]["canonical_id"]
            == "split-seq_test-primer",
            "expected baseline-oligo to preserve canonical ID",
        )

    print("cdna cli smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
