# Changelog

## Unreleased

### Added

- Added `POST /api/extract` as the primary staged v1 endpoint for metadata plus adapter/primer extraction.
- Added `POST /api/one-pass-baseline` for the one-call LLM baseline and removed the legacy `/api/parse` route.
- Added local PDF-to-text extraction for protocol inputs via `scripts/pdf_to_text.py` and Python `pypdf`.
- Added Python-based deterministic sequence inventory extraction for text inputs and extracted PDF text.
- Added the TSV-backed known sequence inventory seed file at `data/sequence_inventory/oligos.tsv`.
- Moved protocol parse support logic for inventory prompt construction and raw model JSON finalization into `scripts/protocol_parse_support.py`.
- Added an LLM audit prompt for human review of suspected missed oligo elements, regex gaps, proposed TSV rows, and proposed extractor changes.
- Added a reduced v1 protocol schema covering metadata, adapter/primer sequences, source spans, and warnings.
- Added extractor coverage for 10x Chromium 5' Section 1-style composite oligos, placeholders, nested primer labels, double-stranded adapters, and subsequence deduplication.
- Added deterministic `/api/extract` artifact writes under `outputs/`: final parsed JSON, final oligo TSV, raw sequence-inventory TSV, and extracted protocol text.
- Added final-output filtering for English-word/PDF-header sequence false positives and LLM audit candidate-review confidence.
- Enforced annotation-only LLM oligo audits; final sequence strings can only come from deterministic extraction.
- Added the Python/Typer `cdna` CLI for customer-safe oligo extraction with one-shot LiteLLM audit, temporary per-run extractor repair, and separate owner/developer curation promotion.
- Added a Python smoke-test script for sequence inventory extraction.
- Removed the obsolete one-time Firestore migration script ahead of the planned Cognee-backed memory adapter.

### Notes

- This is the v1 sequence inventory extractor branch.
- Library Construction Extractor is still deferred.

## v0.2.0

### Changed

- Made `POST /api/parse` JSON-first for protocol parsing.
- Added a scg_lib_structs-style protocol JSON contract with top-level fields:
  - `metadata`
  - `adapter_primer_sequences`
  - `library_generation`
  - `library_sequencing`
  - `read_structure`
  - `final_library_structure`
  - `source_spans`
  - `warnings`
- Added Zod validation for parser output before returning API responses.
- Preserved the previous markdown parser as `parseProtocolMarkdown()` for Slack workflows.
- Updated the Slack bot to keep using markdown parsing for readable thread review.
- Updated the README to document the JSON-first API response.
- Removed web search fallback from the cDNA parse path; `/api/parse` now requires a reachable URL, uploaded file, or pasted text.

### Notes

- This is the v0 protocol parser refinement: the LLM directly emits validated JSON.
