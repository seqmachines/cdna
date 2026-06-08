from __future__ import annotations

import json
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
from cdna_engine.oligos.improve import run_eval_split, run_improve
from cdna_engine.oligos.scanner import parse_input_blocks


app = typer.Typer(help="cDNA parser and curation engine.")
extract_app = typer.Typer(help="Deterministic extraction commands.")
curate_app = typer.Typer(help="Human-gated curation workflows.")
curate_oligos_app = typer.Typer(help="Iterative oligo extractor curation.")
benchmark_app = typer.Typer(help="Benchmark-oriented oligo extraction commands.")
teichlab_app = typer.Typer(help="Teichlab/scg_lib_structs utilities.")
eval_app = typer.Typer(help="Split-level oligo extraction gates.")
console = Console()


@app.command("improve")
def improve_one(
    protocol_id: str = typer.Option(..., "--protocol-id", help="Protocol id, for example drop_seq."),
    input: Path = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol input file or directory."),
    out: Path = typer.Option(..., "--out", "-o", help="Output root for the run package."),
    split: str = typer.Option("train", "--split", help="Split label for this run: train, eval, or test."),
    use_memory: bool = typer.Option(False, "--use-memory/--no-memory", help="Use an explicit runtime memory JSON for sequence completion."),
    memory_path: Optional[Path] = typer.Option(None, "--memory-path", exists=True, readable=True, help="Runtime memory JSON. Required with --use-memory."),
) -> None:
    """Run one protocol through the interactive oligo improvement loop."""
    if use_memory and memory_path is None:
        raise typer.BadParameter("--memory-path is required when --use-memory is set")
    result = run_improve(
        protocol_id=protocol_id,
        input_path=input,
        out=out,
        split=split,
        use_memory=use_memory,
        memory_path=memory_path,
    )
    console.print(result["report"])


@app.command("chunks")
def chunks(
    input: Path = typer.Argument(..., exists=True, readable=True, help="Protocol input file or directory."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional JSON output path. Defaults to stdout."),
) -> None:
    """Print the evidence blocks used by the improve loop."""
    blocks, source_files = parse_input_blocks(input)
    payload = {"source_files": source_files, "blocks": blocks}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"Wrote chunks JSON: {output}")
        return
    typer.echo(text, nl=False)


@extract_app.command("oligos")
def extract_oligos(
    input: list[Path] = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol PDF, XLSX, text, or HTML file. Repeat for grouped benchmark sources."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="Model id used for one-shot audit and temporary repair.", show_default=False),
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
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="Model id used for the audit.", show_default=False),
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
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="Model id used for baseline-oligo.", show_default=False),
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
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m", help="Model id.", show_default=False),
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


def _print_eval_summary(summary: dict) -> None:
    console.print(f"Eval run: {summary['run_id']}")
    console.print(f"Split: {summary['split']}")
    if summary.get("split_file"):
        console.print(f"Split file: {summary['split_file']}")
    console.print(f"Protocols: {summary['count']}")
    aggregate = summary.get("aggregate") or {}
    aggregate_metrics = aggregate.get("metrics") or {}
    if aggregate_metrics:
        recall = aggregate_metrics["oligo_name_recall"]["value"]
        precision = aggregate_metrics["oligo_name_precision"]["value"]
        matched = aggregate_metrics.get("matched_sequence_similarity_mean", {}).get("value", 0.0)
        best = aggregate_metrics.get("sequence_best_match_mean", {}).get("value", 0.0)
        console.print(
            "Aggregate: "
            f"recall={recall:.2f} precision={precision:.2f} "
            f"matched_seq_mean={matched:.2f} seq_best_mean={best:.2f} "
            f"failures={aggregate.get('failure_count', 0)}"
        )
    for item in summary["results"]:
        metrics = item["metrics"]
        recall = metrics["oligo_name_recall"]["value"]
        precision = metrics["oligo_name_precision"]["value"]
        matched = metrics.get("matched_sequence_similarity_mean", {}).get("value", 0.0)
        best = metrics.get("sequence_best_match_mean", {}).get("value", 0.0)
        console.print(
            f"  {item['protocol_id']}: "
            f"recall={recall:.2f} precision={precision:.2f} matched_seq_mean={matched:.2f} "
            f"seq_best_mean={best:.2f} "
            f"failures={item['failure_count']}"
        )
    if summary.get("summary_json"):
        console.print(f"Summary JSON: {summary['summary_json']}")
    if summary.get("summary_markdown"):
        console.print(f"Summary Markdown: {summary['summary_markdown']}")


def _print_eval_progress(event: dict) -> None:
    if event.get("event") == "protocol_start":
        console.print(f"Running {event['index']}/{event['total']}: {event['protocol_id']}")
        return
    if event.get("event") == "protocol_result":
        metrics = event["metrics"]
        recall = metrics["oligo_name_recall"]["value"]
        precision = metrics["oligo_name_precision"]["value"]
        matched = metrics.get("matched_sequence_similarity_mean", {}).get("value", 0.0)
        best = metrics.get("sequence_best_match_mean", {}).get("value", 0.0)
        console.print(
            f"  done {event['protocol_id']}: "
            f"recall={recall:.2f} precision={precision:.2f} "
            f"matched_seq_mean={matched:.2f} seq_best_mean={best:.2f} "
            f"failures={event['failure_count']}"
        )


@eval_app.command("train")
def eval_train(
    protocol_root: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, help="Input root containing protocol directories with groundtruth_oligos.json files."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Maximum number of train protocols to run."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output root for eval run artifacts."),
    split_file: Optional[Path] = typer.Option(None, "--split-file", exists=True, readable=True, help="TSV with Split and protocol_name columns. Defaults to PROTOCOL_ROOT/protocol_split.tsv when present."),
    use_memory: bool = typer.Option(False, "--use-memory/--no-memory", help="Use an explicit runtime memory JSON/TSV for sequence completion."),
    memory_path: Optional[Path] = typer.Option(None, "--memory-path", exists=True, readable=True, help="Runtime memory JSON, TSV, or directory containing one TSV. Required with --use-memory."),
) -> None:
    """Run the train split gate, optionally limited for fast repair loops."""
    if use_memory and memory_path is None:
        raise typer.BadParameter("--memory-path is required when --use-memory is set")
    summary = run_eval_split("train", split_file=split_file, limit=limit, protocol_root=protocol_root, out=out, use_memory=use_memory, memory_path=memory_path, progress=_print_eval_progress)
    _print_eval_summary(summary)


@eval_app.command("eval")
def eval_eval(
    protocol_root: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, help="Input root containing protocol directories with groundtruth_oligos.json files."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output root for eval run artifacts."),
    split_file: Optional[Path] = typer.Option(None, "--split-file", exists=True, readable=True, help="TSV with Split and protocol_name columns. Defaults to PROTOCOL_ROOT/protocol_split.tsv when present."),
    use_memory: bool = typer.Option(False, "--use-memory/--no-memory", help="Use an explicit runtime memory JSON/TSV for sequence completion."),
    memory_path: Optional[Path] = typer.Option(None, "--memory-path", exists=True, readable=True, help="Runtime memory JSON, TSV, or directory containing one TSV. Required with --use-memory."),
) -> None:
    """Run the held-out eval split gate."""
    if use_memory and memory_path is None:
        raise typer.BadParameter("--memory-path is required when --use-memory is set")
    summary = run_eval_split("eval", split_file=split_file, protocol_root=protocol_root, out=out, use_memory=use_memory, memory_path=memory_path, progress=_print_eval_progress)
    _print_eval_summary(summary)


@eval_app.command("test")
def eval_test(
    protocol_root: Path = typer.Argument(..., exists=True, file_okay=False, readable=True, help="Input root containing protocol directories with groundtruth_oligos.json files."),
    frozen: bool = typer.Option(False, "--frozen", help="Required guard for running the frozen test split."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output root for test run artifacts."),
    split_file: Optional[Path] = typer.Option(None, "--split-file", exists=True, readable=True, help="TSV with Split and protocol_name columns. Defaults to PROTOCOL_ROOT/protocol_split.tsv when present."),
    use_memory: bool = typer.Option(False, "--use-memory/--no-memory", help="Use an explicit runtime memory JSON/TSV for sequence completion."),
    memory_path: Optional[Path] = typer.Option(None, "--memory-path", exists=True, readable=True, help="Runtime memory JSON, TSV, or directory containing one TSV. Required with --use-memory."),
) -> None:
    """Run the frozen test split gate."""
    if use_memory and memory_path is None:
        raise typer.BadParameter("--memory-path is required when --use-memory is set")
    try:
        summary = run_eval_split("test", split_file=split_file, frozen=frozen, protocol_root=protocol_root, out=out, use_memory=use_memory, memory_path=memory_path, progress=_print_eval_progress)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_eval_summary(summary)


app.add_typer(extract_app, name="extract")
curate_app.add_typer(curate_oligos_app, name="oligos")
app.add_typer(curate_app, name="curate")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(teichlab_app, name="teichlab")
app.add_typer(eval_app, name="eval")


if __name__ == "__main__":
    app()
