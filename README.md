# cdna

Deterministic symbolic molecular skills for sequencing-library workflows, available as a Python library and an MCP server.

## Molecule schema in ten lines

1. `MoleculeState` has `id`, `label`, `strands`, `origin`, `evidence`, `skill_call_id`, `review_status`, and `stale_since_revision`.
2. `strands` contains ordered `top` and `bottom` lists of `Segment` objects.
3. The top strand runs 5′→3′; the bottom runs 3′→5′, aligned beneath the top.
4. An empty strand list represents an absent strand.
5. Each `Segment` has `name`, `type`, and `origin`; names describe symbolic regions, not base sequences.
6. Segment types: `adapter`, `barcode`, `umi`, `insert`, `primer`, `tso`, `handle`, `index`, `polyA`, `other`.
7. Origins: `source`, `skill`, `memory`, `llm`, `human`; `evidence` is a list of reference strings.
8. `skill_call_id` is a string or `None`; `stale_since_revision` is an integer or `None`.
9. Review status: `unreviewed`, `accepted`, `modified`, `rejected`, or `unresolved`.
10. Pydantic validates the schema strictly, forbids extra fields, and rejects nucleotide strings in symbolic fields.

## Install

Requires Python 3.11 or newer. From this checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The distribution is `cdna-engine`; the import is `cdna`. Runtime dependencies are
Pydantic and the MCP SDK. No database, model credentials, ground truth, or host
application is needed.

## Two skills

| Function / MCP tool | Substrate | Product |
| --- | --- | --- |
| `reverse_transcribe(rna_state, primer)` | Single-stranded RNA on top, containing an `insert` | Copies non-`polyA` segments into the bottom row and places the named primer at its right (5′) end. |
| `template_switch(hybrid, tso)` | An RNA/cDNA hybrid with both rows populated | Adds the named TSO-derived handle at the bottom row's left (3′) end. |

```python
from cdna import MoleculeState, reverse_transcribe, template_switch

rna = MoleculeState.model_validate({
    "id": "S_rna",
    "label": "RNA template",
    "strands": {
        "top": [
            {"name": "transcript", "type": "insert", "origin": "source"},
            {"name": "poly-A tail", "type": "polyA", "origin": "source"},
        ],
        "bottom": [],
    },
    "origin": "source",
    "evidence": ["protocol:page-1"],
    "skill_call_id": None,
    "review_status": "unreviewed",
    "stale_since_revision": None,
})

hybrid = reverse_transcribe(rna, primer="RT primer")
switched = template_switch(hybrid, tso="TSO handle")
assert [s.name for s in switched.strands.bottom] == [
    "TSO handle", "transcript", "RT primer",
]
```

Both functions return a new `MoleculeState`; dictionary inputs are also supported
and return dictionaries. Inputs are never mutated. Identical inputs produce
identical outputs, including a deterministic provisional state ID. Results
preserve evidence and start unreviewed, with `origin="skill"` and no
`skill_call_id`. Hosts assign event IDs and persist states.

Invalid substrates and failed post-conditions raise `ValueError` (including
Pydantic `ValidationError`). The model operates on symbolic segments; it does
not simulate nucleotide-level chemistry.

## Mount the MCP server

The stdio entry point is `cdna-mcp`, equivalent to `python -m cdna.mcp_server`.
Use the absolute path to the executable in the environment where you installed
the package. The host starts the process.

### Claude Code

```sh
claude mcp add --transport stdio cdna -- /absolute/path/to/.venv/bin/cdna-mcp
```

See [Claude Code's stdio configuration](https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server).

### Codex

```sh
codex mcp add cdna -- /absolute/path/to/.venv/bin/cdna-mcp
```

Or add this to `~/.codex/config.toml`:

```toml
[mcp_servers.cdna]
command = "/absolute/path/to/.venv/bin/cdna-mcp"
```

See the [official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

### proofread

Install `cdna-engine` into proofread's Python environment, pinning a released Git
tag in its dependency list. Its existing adapter mounts the library in-process:

```python
from cdna.tools import call_skill, tool_definitions

# Register these names, descriptions, and input schemas with the host.
definitions = tool_definitions()
# Dispatch a host-validated request; returns a state dictionary.
state = call_skill("reverse_transcribe", {
    "rna_state": rna.model_dump(), "primer": "RT primer",
})
```

proofread checks committed substrates and tool access, attaches a skill event ID,
and handles commits. A host using subprocess MCP can instead launch
`cdna-mcp` with the same command as above. The library has no dependency on
proofread.

Both MCP tools return `{"state": ...}` in text and structured content. Errors
return `isError: true` with `{"error": ...}`. Tool discovery advertises the full
input schema.

## Add a skill

1. Implement a pure function in `cdna/skills.py`: a `MoleculeState` and symbolic
   oligo in, a new `MoleculeState` out. Preserve dictionary compatibility when
   dispatching through `cdna.tools`. Do not perform I/O or mutate the substrate.
2. Add a post-condition checker that raises `ValueError` on an invalid product,
   and call it before returning. Keep all chemistry in the skills module.
3. Register the function in `SKILLS` and its operation in `OPERATIONS`. Add its
   argument model, `ARGUMENTS`, `INPUT_NAMES`, and description in `cdna/tools.py`.
   Export the function from `cdna/__init__.py`; the MCP server discovers it
   through the shared tool definitions.
4. Add tests under `tests/` for a known-good product, a rejected substrate, and
   a corrupted product that fails its post-condition. Check determinism, input
   preservation, and MCP dispatch.
5. Update this README and `CHANGELOG.md`. Changes to `cdna/molecule.py` require
   human approval and a major version bump; see [AGENTS.md](AGENTS.md).

Run the tests:

```sh
python -m pip install '.[test]'
python -m pytest
```
