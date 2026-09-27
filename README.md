# cDNA

Self-improving AI agent for sequencing protocol

## Setup

```bash
npm install
python3 -m pip install -e .
```

Create `.env.local`:

```
GOOGLE_GENERATIVE_AI_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

Get a Gemini key from [Google AI Studio](https://aistudio.google.com/apikey) for the general model bridge used by other commands. Add `OPENAI_API_KEY` or `CODEX_API_KEY` for the coding-agent linker used by `cdna improve`.

## Usage

```bash
npm run dev
```

## Symbolic molecular skills (M13)

`cdna-engine` also installs the independent `cdna` Python package and the
`cdna-mcp` stdio server. These tools operate on symbolic segments, with the
top strand listed 5′→3′ and bottom 3′→5′. They require no database, proofread,
model credentials, or ground truth. The existing oligo-extraction CLI remains
available as `cdna`.

```sh
python3.11 -m pip install .
cdna-mcp
# Equivalent: python3.11 -m cdna.mcp_server
```

The MCP server exposes `reverse_transcribe(rna_state, primer)` and
`template_switch(hybrid, tso)`. Both return `{state: MoleculeState}` without
persisting it; a host such as proofread supplies event IDs and commits. Inputs
use the full symbolic state schema advertised by the server. Nucleotide
strings are rejected. Error results have `isError: true` and `{error: ...}`.

Example MCP configuration (replace the Python path with the environment where
you installed this package):

```json
{"mcpServers":{"cdna":{"command":"/absolute/path/to/python","args":["-m","cdna.mcp_server"]}}}
```

With that JSON saved as `/tmp/cdna-mcp.json`, Claude Code 2.1.221 or later can
use only this server (earlier print mode can race MCP startup):

```sh
claude -p --strict-mcp-config --mcp-config /tmp/cdna-mcp.json \
  --tools '' --allowedTools mcp__cdna__template_switch \
  --permission-mode dontAsk 'Use template_switch on the symbolic hybrid supplied in this prompt: ...'
```

Proofread installs a wheel built from this repository and mounts these same
tool definitions. Its adapter checks committed substrates and harness access,
then records skill events; this package has no dependency on that adapter.
See the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
and [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
for host configuration.
