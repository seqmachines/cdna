# Changelog

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
