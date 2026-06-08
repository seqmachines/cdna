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
