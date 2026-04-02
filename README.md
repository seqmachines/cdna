# cDNA

Parse sequencing protocol documents into structured markdown with ASCII library diagrams. Supports multiple LLM providers via the Vercel AI Gateway.

## Setup

```bash
npm install
```

Create `.env.local`:

```
GOOGLE_GENERATIVE_AI_API_KEY=your-key-here

# Optional: enables Google Search fallback when URLs are inaccessible
GOOGLE_SEARCH_API_KEY=your-key-here
GOOGLE_SEARCH_ENGINE_ID=your-cx-id
```

Get a Gemini key from [Google AI Studio](https://aistudio.google.com/apikey). For the search fallback, create a [Programmable Search Engine](https://programmablesearchengine.google.com) (enable "Search the entire web") and enable the [Custom Search API](https://console.cloud.google.com/apis/library/customsearch.googleapis.com) in Cloud Console (free: 100 queries/day).

## Usage

```bash
npm run dev
```

Open `http://localhost:3000`:

- **Parse** — submit a URL, upload a file (PDF/Word/Excel), or paste protocol text
- **Model** — pick any model from the dropdown (fetched live from Vercel AI Gateway)
- **Preview** — review the parsed output with rendered ASCII diagrams
- **Publish** — save to the protocols library

Browse published protocols at `/protocols`.

The parse API has two smart fallbacks:
- **PDF-to-text** — if a model can't read PDF binary, the file is automatically converted to text and retried
- **Web search** — the LLM has `web_search` and `fetch_url` tools so it can find protocol information when the provided content is insufficient or the URL is inaccessible. Requires `GOOGLE_SEARCH_API_KEY` in `.env.local`.

## Slack bot

A Slack bot lets users parse protocols and ask follow-up questions directly in Slack.

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

## How it works

The parser sends protocol documents directly to the LLM along with a system prompt (`skills/SKILL.md`) and a reference example (`skills/references/example-output.md`). The LLM reads the document natively (no text extraction) and returns structured markdown covering:

1. **Metadata** — kit name, chemistry version, document reference
2. **Adapter/primer sequences** — every oligo in 5'→3' orientation
3. **Step-by-step library construction** — with ASCII diagrams showing molecular products
4. **Sequencing read configuration** — primer binding, read direction, cycle counts

Parsed protocols are stored as markdown files in `protocols/`.

## Stack

- Next.js (App Router) + TypeScript + Tailwind CSS
- Vercel AI SDK + AI Gateway (dynamic model selection)
- Vercel Chat SDK (Slack bot)
