# cdna — agent instructions

cdna is a Python library of deterministic molecular skills for sequencing-library workflows, exposed as an MCP server. It has no database, no model credentials, no ground truth, and no dependency on any host. proofread (and, later, Harbor tasks and other agents) install it.

Distribution name `cdna-engine`, import name `cdna`. Entry point `cdna-mcp` (`python -m cdna.mcp_server`).

## Scope
- You own this repo only. Never edit `../proofread` or `../libstruct-bench`.
- Layout: `cdna/` (molecule model, skills, mcp_server, memory later), `tests/`, `pyproject.toml`, `README.md`, `CHANGELOG.md`. Nothing else at the root.

## Contract
- `cdna/molecule.py` is the schema shared with proofread (its SPEC.md §2.1): symbolic segments, top strand 5′→3′, bottom strand 3′→5′ aligned under top, no nucleotide strings. Any change to it is a contract change: describe it, stop, wait for human approval, then bump the major version and note it in CHANGELOG.md.
- A skill is a pure function: `MoleculeState` (+ oligo) in, `MoleculeState` out, plus a post-condition check that raises on an invalid product. No I/O, no logging, no side effects. The MCP layer wraps skills; it never contains chemistry.
- Nucleotide strings are rejected at the schema boundary. Error results carry `isError: true` and `{error: ...}`.

## Working rules
- One task per session; stop at its done-when test; commit with explicit paths, never `git add -A`, never amend.
- Every skill ships with tests: a known-good product, a rejected substrate, and the post-condition failing on a corrupted product.
- Tag releases `vX.Y.Z`; proofread pins by tag. Do not change a tagged release.
- Read-only reference: `../libstruct-bench/schemas/` for the Task 3 representation the molecule model mirrors.