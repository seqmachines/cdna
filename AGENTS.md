# cDNA Oligo Extraction Agent

## Current Scope

This repo currently implements only:

- oligo extraction
- adapter extraction
- primer extraction
- memory-assisted sequence completion
- ground-truth comparison
- human review loop
- Codex repair loop

Do not implement:

- SOP generation
- QC checklist generation
- full protocol step graph
- Cell Ranger / STARsolo / Scanpy config generation

## Main Workflow

Prefer the one-command workflow:

```bash
cdna improve
```
