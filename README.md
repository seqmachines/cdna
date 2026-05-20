# cDNA

Parse sequencing protocol documents into structured scg_lib_structs-style JSON. The web app runs on Next.js/Vercel, while offline parser curation runs through the Python `cdna` CLI with LiteLLM.

## Setup

```bash
npm install
python3 -m pip install -e .
```

Create `.env.local`:

```
GOOGLE_GENERATIVE_AI_API_KEY=your-key-here
```

Get a Gemini key from [Google AI Studio](https://aistudio.google.com/apikey). The Python `cdna` CLI maps this existing key to LiteLLM's `GEMINI_API_KEY` variable at runtime.

## Usage

```bash
npm run dev
```

Open `http://localhost:3000`.

The primary v1 extractor is available at `POST /api/extract`. It accepts a URL, uploaded file, or pasted protocol text and returns validated JSON with protocol metadata plus adapter/primer sequences:

```bash
curl -X POST http://localhost:3000/api/extract \
  -F "text=Poly-dT RT primer: 5'- AAGCAGTGGTATCAACGCAGAGTAC TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN -3'" \
  -F "model=google/gemini-3.1-pro-preview"
```

Response:

```json
{
  "protocol": {
    "metadata": {
      "modality": [],
      "category": [],
      "inputs": [],
      "outputs": [],
      "cost": null,
      "time": null
    },
    "adapter_primer_sequences": [],
    "source_spans": {},
    "warnings": []
  },
  "raw": "..."
}
```

The one-call LLM baseline is available at `POST /api/one-pass-baseline`.

The extraction APIs do not use web search fallback. Provide a reachable URL, uploaded file, or pasted protocol text.

PDF inputs are converted to text locally with Python `pypdf` before extraction. Text inputs and extracted PDF text go through Python-based deterministic sequence inventory extraction. The staged `/api/extract` path uses the deterministic oligo list as the returned adapter/primer source of truth, while the LLM extracts metadata and audits suspected missed oligos.

Each successful `/api/extract` run writes deterministic local artifacts under `outputs/`:

- `<source>.extract.json` — the final parsed API response after schema validation.
- `<source>.final-oligos.tsv` — the final shaped oligo table from `protocol.adapter_primer_sequences`.
- `<source>.sequence-inventory.tsv` — every deterministic sequence candidate from `scripts/sequence_inventory.py`.
- `<source>.protocol.txt` — the extracted text used for deterministic extraction and LLM metadata parsing.

The raw `sequence-inventory.tsv` may include debug candidates that are later rejected from the final output. Its score column is `heuristic_score`, not model confidence. The final JSON and `final-oligos.tsv` use deterministic filters to remove English-word/PDF-header false positives, and use LLM audit `candidate_reviews[].confidence` when the audit returns per-candidate confidence.

The LLM audit is annotation-only for oligos: it may accept, reject, flag for review, suggest names/roles, and assign confidence to existing deterministic candidates. It must not generate or modify sequence strings, and the server ignores any sequence string returned in `candidate_reviews`.

Known adapter and primer elements are seeded in `data/sequence_inventory/oligos.tsv`. The extractor merges known inventory hits with deterministic sequence candidates, deduplicates subsequence hits, and returns advisory LLM audit findings for human review only. The LLM does not modify the TSV or extractor code.

To smoke-test sequence inventory extraction:

```bash
python3 scripts/test_sequence_inventory.py
```

## Oligo curation loop

Use the Python/Typer curation loop when an LLM audit spots adapter/primer terms that the deterministic extractor missed. The Next.js API does not mutate code or the oligo database during a request; this command writes review artifacts first.

```bash
cdna curate oligos \
  --input /path/to/protocol.pdf \
  --model gemini/gemini-3.1-pro-preview \
  --max-iterations 5
```

Artifacts are written under `outputs/curation/<protocol>/`:

- `iteration-N.protocol.txt` — text used for extraction and audit.
- `iteration-N.sequence-inventory.tsv` — deterministic candidates from `scripts/sequence_inventory.py`.
- `iteration-N.audit.json` — LLM audit of named oligo terms, candidate reviews, and missing cases.
- `iteration-N.missing-oligos.tsv` — human-readable missing/term checklist.
- `iteration-N.proposed-extractor.patch` — review-only patch proposal for the deterministic extractor.
- `iteration-N.pending-inventory.tsv` — review-only TSV rows for possible DB additions.

The LLM may propose patches and pending rows, but it may not generate or modify sequence strings. To approve a proposal, review the artifacts, copy accepted changes to `approved-extractor.patch` and/or `approved-inventory.tsv` in the same curation directory, then resume:

```bash
cdna curate oligos resume \
  --run-dir outputs/curation/<protocol>
```

To run deterministic oligo extraction without the LLM audit loop:

```bash
cdna extract oligos --input /path/to/protocol.pdf --output outputs/protocol.sequence-inventory.tsv
```

## Slack bot

A Slack bot lets users parse protocols and ask follow-up questions directly in Slack. The Slack workflow still uses the markdown parser so protocol reviews remain readable in threads.

### Setup

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add bot scopes: `chat:write`, `users:read`, `app_mentions:read`, `channels:history`, `groups:history`, `im:history`
3. Enable Event Subscriptions, set the request URL to `https://<your-domain>/api/slack`
4. Subscribe to `app_mention` and `message.im` events
5. Install the app to your workspace
6. Add to `.env.local`:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_SIGNING_SECRET=...
   ```

### Commands

- `@cDNA <url>` — parse a protocol (defaults to `google/gemini-3.1-pro-preview`)
- `@cDNA parse with claude <url>` — parse with a specific model
- Reply with a question — follow-up Q&A using the parsed protocol as context
- Reply `publish` — save to the protocols library

## Benchmark API

`POST /api/benchmark` returns structured JSON for evaluating library structure extraction. Accepts the same inputs as `/api/extract` (url, file, text, model) via FormData. No search tools or fallback — the benchmark script controls the input format.

```bash
curl -X POST http://localhost:3000/api/benchmark \
  -F "url=https://cdn.10xgenomics.com/image/upload/v1725314293/support-documents/CG000731_ChromiumGEM-X_SingleCell3v4_UserGuide_RevB.pdf" \
  -F "model=google/gemini-3.1-pro-preview"
```

Response:

```json
{
  "result": {
    "protocol_name": "Chromium Single Cell 3' Gene Expression v4",
    "library_sequence": "AATGATACGGCGACCACCGAG...",
    "segments": [...],
    "placeholder_key": { "B": "cell barcode", "U": "UMI", "I": "sample index" }
  },
  "raw": "..."
}
```

Variable regions use typed placeholders (`B`=barcode, `U`=UMI, `I`=index, `L`=ligation, `R`=RT, `T`=Tn5, `X`=linker, `V`=capture) instead of generic `N`.

See [`CHANGELOG.md`](CHANGELOG.md) for implementation updates.

## Stack

- Next.js (App Router) + TypeScript + Tailwind CSS
- Vercel AI SDK + AI Gateway (dynamic model selection)
- Vercel Chat SDK (Slack bot)
