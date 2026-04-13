import { generateText, stepCountIs } from "ai";
import { loadBenchmarkPrompt } from "./load-benchmark-prompt";
import { resolveModel, DEFAULT_MODEL } from "./models";
import { webSearchTool, fetchUrlTool } from "./tools";
import type { UserContent } from "ai";

const MIME_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".doc": "application/msword",
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xls": "application/vnd.ms-excel",
  ".xlsx":
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

function getMimeType(filename: string): string {
  const ext = filename.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  return MIME_TYPES[ext] || "application/octet-stream";
}

function extractJSON(text: string): Record<string, unknown> | null {
  // Try direct parse
  try { return JSON.parse(text); } catch { }
  // Strip markdown code fences
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
  if (fenceMatch) { try { return JSON.parse(fenceMatch[1]); } catch { } }
  // Find first { ... } block
  const braceMatch = text.match(/\{[\s\S]*\}/);
  if (braceMatch) { try { return JSON.parse(braceMatch[0]); } catch { } }
  return null;
}

export async function parseBenchmark(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  modelId?: string,
  useTools: boolean = false,
) {
  const systemPrompt = loadBenchmarkPrompt();

  const userContent: UserContent = [];

  if (options.fileData) {
    userContent.push({
      type: "file",
      data: new Uint8Array(options.fileData),
      mediaType: getMimeType(options.fileName || source),
    });
  }

  let promptText = `Parse this sequencing protocol and output ONLY a JSON object with the library structure.\n\nSource: ${source}`;

  if (options.text) {
    promptText += `\n\nProtocol content:\n${options.text}`;
  }

  userContent.push({ type: "text", text: promptText });

  const generateOptions: Parameters<typeof generateText>[0] = {
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: systemPrompt,
    messages: [{ role: "user", content: userContent }],
  };

  if (useTools) {
    generateOptions.tools = {
      web_search: webSearchTool,
      fetch_url: fetchUrlTool,
    };
    generateOptions.stopWhen = stepCountIs(12);
  }

  const result = await generateText(generateOptions);

  // If final text is empty (e.g., model exhausted step budget on tool calls),
  // scan backwards through steps for the last non-empty text.
  let text = result.text;
  if (!text && Array.isArray(result.steps)) {
    for (let i = result.steps.length - 1; i >= 0; i--) {
      const stepText = result.steps[i]?.text;
      if (stepText) {
        text = stepText;
        break;
      }
    }
  }

  const parsed = extractJSON(text);

  return {
    result: parsed,
    raw: text,
  };
}
