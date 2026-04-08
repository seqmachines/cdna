import { generateText, stepCountIs } from "ai";
import { loadSkill } from "./load-skill";
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

export async function parseProtocol(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  modelId?: string
) {
  const { skillMd, exampleMd } = loadSkill();

  const systemPrompt = `${skillMd}

## Reference example

Below is a complete worked example showing the exact output format, level of detail, and ASCII diagram style to target:

${exampleMd}`;

  const userContent: UserContent = [];

  if (options.fileData) {
    userContent.push({
      type: "file",
      data: new Uint8Array(options.fileData),
      mediaType: getMimeType(options.fileName || source),
    });
  }

  let promptText = `Parse this sequencing protocol and extract the complete library structure.

Source: ${source}`;

  if (options.text) {
    promptText += `

Protocol content:
${options.text}`;
  }

  userContent.push({ type: "text", text: promptText });

  const tools = {
    web_search: webSearchTool,
    fetch_url: fetchUrlTool,
  };

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: systemPrompt,
    messages: [{ role: "user", content: userContent }],
    tools,
    stopWhen: stepCountIs(5),
  });

  return text;
}
