import { generateText } from "ai";
import { google } from "@ai-sdk/google";

const DEFAULT_MODEL = "google/gemini-3.1-pro-preview";

function resolveModel(modelId) {
  const requested = modelId || DEFAULT_MODEL;
  if (requested.startsWith("google/")) {
    return google(requested.replace("google/", ""));
  }
  if (requested.startsWith("gemini/")) {
    return google(requested.replace("gemini/", ""));
  }
  return requested;
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const result = await generateText({
    model: resolveModel(payload.model),
    system: payload.system || "",
    prompt: payload.prompt || "",
  });
  process.stdout.write(JSON.stringify({ text: result.text }) + "\n");
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(message + "\n");
  process.exit(1);
});
