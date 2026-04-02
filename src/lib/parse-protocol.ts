import { generateText, stepCountIs } from "ai";
import { loadSkill } from "./load-skill";
import { resolveModel, DEFAULT_MODEL } from "./models";
import { pdfToText } from "./pdf-to-text";
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

  let text: string;
  try {
    const result = await generateText({
      model: resolveModel(modelId || DEFAULT_MODEL),
      system: systemPrompt,
      messages: [{ role: "user", content: userContent }],
      tools,
      stopWhen: stepCountIs(5),
    });
    text = result.text;
  } catch (err) {
    if (options.fileData && options.fileName?.toLowerCase().endsWith(".pdf")) {
      console.log("  Model can't read PDF, converting to text and retrying...");
      const extracted = await pdfToText(options.fileData);
      const textContent = options.text
        ? options.text + "\n\n" + extracted
        : extracted;

      const fallbackContent: UserContent = [{ type: "text", text: textContent }];

      const result = await generateText({
        model: resolveModel(modelId || DEFAULT_MODEL),
        system: systemPrompt,
        messages: [{ role: "user", content: fallbackContent }],
        tools,
        stopWhen: stepCountIs(5),
      });
      text = result.text;
    } else {
      throw err;
    }
  }

  return text;
}
