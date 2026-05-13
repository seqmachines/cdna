# cDNA

Parse sequencing protocol documents into structured scg_lib_structs-style JSON. Supports multiple LLM providers via the Vercel AI Gateway.

## Setup

```bash
npm install
```

Create `.env.local`:

```
GOOGLE_GENERATIVE_AI_API_KEY=your-key-here
```

Get a Gemini key from [Google AI Studio](https://aistudio.google.com/apikey). 

## Usage

```bash
npm run dev
```

Open `http://localhost:3000`.

The primary parser is available at `POST /api/parse`. It accepts a URL, uploaded file, or pasted protocol text and returns validated JSON:

```bash
curl -X POST http://localhost:3000/api/parse \
  -F "text=10x Chromium Single Cell 3' v3.1 uses Read 1 for 16 bp cell barcode and 12 bp UMI, Read 2 for cDNA insert, and i7/i5 sample indexes." \
  -F "model=google/gemini-3.1-pro-preview"
```

Response:

```json
{
  "protocol": {
    "metadata": {},
    "adapter_primer_sequences": [],
    "library_generation": [],
    "library_sequencing": [],
    "read_structure": {},
    "final_library_structure": {},
    "source_spans": {},
    "warnings": []
  },
  "raw": "..."
}
```

The parse API does not use web search fallback. Provide a reachable URL, uploaded file, or pasted protocol text.

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

`POST /api/benchmark` returns structured JSON for evaluating library structure extraction. Accepts the same inputs as `/api/parse` (url, file, text, model) via FormData. No search tools or fallback — the benchmark script controls the input format.

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
