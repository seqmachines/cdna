# cDNA

Parse sequencing protocol documents into structured markdown with ASCII library diagrams. Supports multiple LLM providers via the Vercel AI Gateway.

## Setup

```bash
npm install
```

Create `.env.local`:

```
GOOGLE_GENERATIVE_AI_API_KEY=your-key-here
```

Get a key from [Google AI Studio](https://aistudio.google.com/apikey).

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
