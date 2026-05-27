from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from cdna_engine.oligos.curate import DEFAULT_MODEL, resume_curation, run_curation
from cdna_engine.oligos.benchmark import (
    baseline_oligo_extract,
    extract_name_guided_deterministic,
    load_candidates,
)
from cdna_engine.oligos.codex_update import run_codex_updated_extraction
from cdna_engine.oligos.extract import extract_oligos_customer


app = typer.Typer(help="cDNA parser and curation engine.")
extract_app = typer.Typer(help="Deterministic extraction commands.")
curate_app = typer.Typer(help="Human-gated curation workflows.")
curate_oligos_app = typer.Typer(help="Iterative oligo extractor curation.")
benchmark_app = typer.Typer(help="Benchmark-oriented oligo extraction commands.")
teichlab_app = typer.Typer(help="Teichlab/scg_lib_structs utilities.")
console = Console()


@extract_app.command("oligos")
def extract_oligos(
    input: list[Path] = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol PDF, XLSX, text, or HTML file. Repeat for grouped benchmark sources."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="LiteLLM model id used for one-shot audit and temporary repair."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Final oligo TSV output path."),
    artifacts_dir: Optional[Path] = typer.Option(None, "--artifacts-dir", help="Directory for all extraction artifacts."),
    deterministic_only: bool = typer.Option(False, "--deterministic-only", help="Skip LLM audit and temporary repair."),
    protocol_slug: Optional[str] = typer.Option(None, "--protocol-slug", help="Protocol slug for name-guided benchmark extraction."),
    candidate_json: Optional[Path] = typer.Option(None, "--candidate-json", exists=True, readable=True, help="Optional name-only candidate JSON for name-guided benchmark extraction."),
    benchmark_json_output: Optional[Path] = typer.Option(None, "--benchmark-json-output", help="Destination JSON for name-guided benchmark extraction."),
    use_codex_update: bool = typer.Option(False, "--use-codex-update", help="Ask Codex to generate a run-local inventory script before benchmark extraction."),
    codex_out: Optional[Path] = typer.Option(None, "--codex-out", help="Directory for Codex prompt, generated script, result, and cDNA output."),
    apply_to_cdna: bool = typer.Option(False, "--apply-to-cdna", help="Deprecated; Codex v1 now writes run-local generated scripts only."),
    codex_dry_run: bool = typer.Option(False, "--codex-dry-run", help="Write the Codex prompt without calling Codex or running extraction."),
    cdna_repo: Optional[Path] = typer.Option(None, "--cdna-repo", exists=True, file_okay=False, readable=True, help="Optional cDNA repo root to read the master inventory script from."),
    codex_model: Optional[str] = typer.Option(None, "--codex-model", help="Codex agent model, for example gpt-5.5, gpt-5.4, or gpt-5.3-codex."),
    codex_reasoning_effort: Optional[str] = typer.Option(None, "--codex-reasoning-effort", help="Codex reasoning effort: minimal, low, medium, high, or xhigh."),
) -> None:
    """Extract final oligo sequences with one LLM audit and temporary per-run repair."""
    if candidate_json is not None or protocol_slug is not None or use_codex_update:
        if protocol_slug is None:
            raise typer.BadParameter("--protocol-slug is required for benchmark extraction")
        if use_codex_update:
            if apply_to_cdna:
                raise typer.BadParameter("--apply-to-cdna is no longer supported; Codex v1 writes a run-local generated script")
            destination = benchmark_json_output or output or Path(f"{protocol_slug}.extract.json")
            codex_dir = codex_out or (artifacts_dir or destination.parent) / "codex-update"
            result = run_codex_updated_extraction(
                protocol_slug=protocol_slug,
                inputs=input,
                candidate_json=candidate_json,
                output=destination,
                artifacts_dir=artifacts_dir,
                codex_out=codex_dir,
                repo_root=cdna_repo,
                apply_to_repo=apply_to_cdna,
                dry_run=codex_dry_run,
                deterministic_only=deterministic_only,
                model=model,
                codex_model=codex_model,
                codex_reasoning_effort=codex_reasoning_effort,
            )
            console.print(f"Codex update status: {result['status']}")
            if result.get("codex_sdk_version"):
                console.print(f"Codex SDK: @openai/codex-sdk {result['codex_sdk_version']}")
            if result.get("codex_model"):
                console.print(f"Codex model: {result['codex_model']}")
            if result.get("codex_reasoning_effort"):
                console.print(f"Codex reasoning effort: {result['codex_reasoning_effort']}")
            if result.get("audit_model"):
                console.print(f"LLM audit model: {result['audit_model']}")
            if "generated_script" in result and Path(result["generated_script"]).exists():
                console.print(f"Generated script: {result['generated_script']}")
            if "prediction_record_count" in result:
                console.print(f"Extracted rows: {result['prediction_record_count']}")
            if result.get("audit_status"):
                console.print(f"Audit status: {result['audit_status']}")
            return
        if apply_to_cdna or codex_dry_run or codex_out or cdna_repo or codex_model or codex_reasoning_effort:
            raise typer.BadParameter("--apply-to-cdna, --codex-dry-run, --codex-out, --cdna-repo, --codex-model, and --codex-reasoning-effort require --use-codex-update")
        result = extract_name_guided_deterministic(
            protocol_slug=protocol_slug,
            inputs=input,
            candidates=load_candidates(candidate_json) if candidate_json else [],
            output=benchmark_json_output or output or Path(f"{protocol_slug}.extract.json"),
            artifacts_dir=artifacts_dir,
            model=model,
            deterministic_only=True,
        )
        console.print(f"Wrote deterministic v1 benchmark JSON: {benchmark_json_output or output or Path(f'{protocol_slug}.extract.json')}")
        console.print(f"Extracted rows: {len(result['protocol']['adapter_primer_sequences'])}")
        return
    if len(input) != 1:
        raise typer.BadParameter("multiple --input values require --candidate-json benchmark mode")
    result = extract_oligos_customer(
        input_path=input[0],
        model=model,
        output=output,
        artifacts_dir=artifacts_dir,
        deterministic_only=deterministic_only,
    )
    artifacts = result["artifacts"]
    console.print(f"Wrote final oligo TSV: {artifacts['final_oligo_tsv']}")
    console.print(f"Wrote final JSON: {artifacts['final_json']}")
    console.print(f"Repair status: {result['repair_status']}")


@benchmark_app.command("deterministic-oligos")
def benchmark_deterministic_oligos(
    protocol_slug: str = typer.Option(..., "--protocol-slug", help="Protocol slug for the grouped source files."),
    candidate_json: Path = typer.Option(..., "--candidate-json", exists=True, readable=True, help="Name-only candidate JSON."),
    input: list[Path] = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol source file. Repeat for grouped sources."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination benchmark JSON."),
    artifacts_dir: Optional[Path] = typer.Option(None, "--artifacts-dir", help="Directory for protocol text, inventory, and audit artifacts."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="LiteLLM model id used for the audit."),
    deterministic_only: bool = typer.Option(False, "--deterministic-only", help="Skip the LLM audit."),
) -> None:
    """Run name-guided deterministic oligo extraction with optional LLM audit."""
    result = extract_name_guided_deterministic(
        protocol_slug=protocol_slug,
        inputs=input,
        candidates=load_candidates(candidate_json),
        output=output,
        artifacts_dir=artifacts_dir,
        model=model,
        deterministic_only=deterministic_only,
    )
    console.print(f"Wrote deterministic benchmark JSON: {output}")
    console.print(f"Extracted rows: {len(result['protocol']['adapter_primer_sequences'])}")


@benchmark_app.command("baseline-oligo")
def benchmark_baseline_oligo(
    protocol_slug: str = typer.Option(..., "--protocol-slug", help="Protocol slug for the grouped source files."),
    candidate_json: Path = typer.Option(..., "--candidate-json", exists=True, readable=True, help="Name-only candidate JSON."),
    input: list[Path] = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol source file. Repeat for grouped sources."),
    output: Path = typer.Option(..., "--output", "-o", help="Destination benchmark JSON."),
    artifacts_dir: Optional[Path] = typer.Option(None, "--artifacts-dir", help="Directory for protocol text, prompt, and raw response artifacts."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="LiteLLM model id used for baseline-oligo."),
    protocol_text_limit: int = typer.Option(250_000, "--protocol-text-limit", min=0, help="Maximum protocol source characters sent to the baseline LLM. Use 0 for full text."),
) -> None:
    """Run the name-guided LLM oligo baseline."""
    result = baseline_oligo_extract(
        protocol_slug=protocol_slug,
        inputs=input,
        candidates=load_candidates(candidate_json),
        output=output,
        artifacts_dir=artifacts_dir,
        model=model,
        protocol_text_limit=protocol_text_limit,
    )
    console.print(f"Wrote baseline-oligo benchmark JSON: {output}")
    console.print(f"Extracted rows: {len(result['protocol']['adapter_primer_sequences'])}")


@curate_oligos_app.callback(invoke_without_command=True)
def curate_oligos(
    ctx: typer.Context,
    input: Optional[Path] = typer.Option(None, "--input", "-i", exists=True, readable=True, help="Protocol PDF, text, or HTML file."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="LiteLLM model id, e.g. gemini/gemini-3.1-pro-preview."),
    max_iterations: int = typer.Option(5, "--max-iterations", min=1, help="Maximum audit/repair iterations."),
    run_dir: Optional[Path] = typer.Option(None, "--run-dir", help="Optional curation output directory."),
) -> None:
    """Run the LLM audit loop until it passes or needs human approval."""
    if ctx.invoked_subcommand is not None:
        return
    if input is None:
        raise typer.BadParameter("--input is required unless using a subcommand")
    run_curation(input, model=model, max_iterations=max_iterations, run_dir=run_dir)


@curate_oligos_app.command("resume")
def resume_oligos(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False, readable=True, help="Curation run directory."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model from state.json."),
    max_iterations: Optional[int] = typer.Option(None, "--max-iterations", min=1, help="Override max iterations from state.json."),
) -> None:
    """Apply approved artifacts and continue the oligo audit loop."""
    resume_curation(run_dir=run_dir, model=model, max_iterations=max_iterations)


@curate_oligos_app.command("promote")
def promote_oligos(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False, readable=True, help="Reviewed curation run directory."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model from state.json."),
    max_iterations: Optional[int] = typer.Option(None, "--max-iterations", min=1, help="Override max iterations from state.json."),
) -> None:
    """Owner/developer path for applying approved extractor or inventory changes."""
    resume_curation(run_dir=run_dir, model=model, max_iterations=max_iterations)


@teichlab_app.command("build-db")
def teichlab_build_db(
    out: Path = typer.Option(..., "--out", help="Destination reviewed oligo TSV path."),
) -> None:
    """Placeholder for the curated Teichlab oligo DB pipeline."""
    console.print(f"Teichlab DB build is planned next. Requested output: {out}")
    raise typer.Exit(0)


app.add_typer(extract_app, name="extract")
curate_app.add_typer(curate_oligos_app, name="oligos")
app.add_typer(curate_app, name="curate")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(teichlab_app, name="teichlab")


if __name__ == "__main__":
    app()
