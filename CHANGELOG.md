# Changelog

## Unreleased

### Added

- Added local PDF-to-text extraction for `/api/parse` via `scripts/pdf_to_text.py` and Python `pypdf`.
- Added Python-based deterministic sequence inventory extraction for text inputs and extracted PDF text.
- Moved protocol parse support logic for inventory prompt construction and raw model JSON finalization into `scripts/protocol_parse_support.py`.
- Added an LLM audit prompt that treats extracted sequence candidates as authoritative for `adapter_primer_sequences`.
- Added a post-parse guard that rejects non-null adapter/primer sequences not found in the deterministic inventory.

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
