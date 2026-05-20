from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from cdna_engine.io import prepare_protocol_text
from cdna_engine.oligos.curate import DEFAULT_MODEL, resume_curation, run_curation
from cdna_engine.oligos.inventory import extract_sequence_inventory, inventory_tsv


app = typer.Typer(help="cDNA parser and curation engine.")
extract_app = typer.Typer(help="Deterministic extraction commands.")
curate_app = typer.Typer(help="Human-gated curation workflows.")
curate_oligos_app = typer.Typer(help="Iterative oligo extractor curation.")
teichlab_app = typer.Typer(help="Teichlab/scg_lib_structs utilities.")
console = Console()


@extract_app.command("oligos")
def extract_oligos(
    input: Path = typer.Option(..., "--input", "-i", exists=True, readable=True, help="Protocol PDF, text, or HTML file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Optional TSV output path."),
) -> None:
    """Run deterministic oligo extraction and print/write the sequence inventory TSV."""
    text = prepare_protocol_text(input)
    inventory = extract_sequence_inventory(text)
    tsv = inventory_tsv(inventory)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(tsv, encoding="utf-8")
        console.print(f"Wrote {output}")
    else:
        console.print(tsv, end="")


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
app.add_typer(teichlab_app, name="teichlab")


if __name__ == "__main__":
    app()
