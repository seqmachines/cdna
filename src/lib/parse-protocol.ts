import { generateText, stepCountIs } from "ai";
import { loadSkill } from "./load-skill";
import { resolveModel, DEFAULT_MODEL } from "./models";
import { ProtocolSchema, type ProtocolParseResult } from "./protocol-schema";
import { buildProtocolContext, finalizeProtocolFromModel } from "./python-protocol-tools";
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

function protocolJsonPrompt() {
  return `You are cDNA, a sequencing protocol parser.

Return ONLY valid JSON matching this exact top-level object:
{
  "metadata": {},
  "adapter_primer_sequences": [],
  "library_generation": [],
  "library_sequencing": [],
  "read_structure": {},
  "final_library_structure": {},
  "source_spans": {},
  "warnings": []
}

Rules:
- Do not return Markdown, prose, code fences, or explanations outside JSON.
- Parse sequencing protocols into a scg_lib_structs-style structured representation.
- adapter_primer_sequences must contain exact copied sequences from the source only.
- Never invent adapter, primer, barcode, UMI, or index sequences.
- If a named sequence is mentioned but exact bases are missing, set "sequence": null and add a warning.
- Preserve modifications when present, such as rG, /5Phos/, /5Biosg/, phosphorothioates, (T)30, or degenerate bases.
- Use source_spans to store copied evidence text. Each critical claim should cite source_span_ids.
- library_generation must be ordered by molecular construction step.
- library_sequencing must describe read names, platform, primers, direction, cycles, template strand, and what each read captures when available.
- read_structure should be compact and machine-readable, keyed by R1, R2, I1, I2 when available.
- final_library_structure should contain ordered segments. Use exact bases only if source-backed; use null for unknown biological inserts or missing exact sequences.
- warnings should list missing sequences, ambiguous orientation, unsupported claims, or inferred details.

Required object shapes:
- adapter_primer_sequences[]: { "name": string, "role": string|null, "sequence": string|null, "orientation": "5_to_3"|"3_to_5"|"unknown"|null, "modifications": string[], "source_span_ids": string[], "uncertainty": string|null }
- library_generation[]: { "step_number": number, "name": string, "operation": string|null, "inputs": string[], "outputs": string[], "product_structure": string|null, "used_sequence_names": string[], "conditions": string[], "source_span_ids": string[] }
- library_sequencing[]: { "read_name": string, "platform": string|null, "primer": string|null, "direction": string|null, "cycles": number|null, "template_strand": string|null, "what_is_read": string[], "source_span_ids": string[] }
- read_structure: { "R1": [{ "name": string, "start": number|null, "end": number|null, "source_span_ids": string[] }], "R2": [], "I1": [], "I2": [] }
- final_library_structure: { "orientation": string|null, "segments": [{ "name": string, "type": string, "sequence": string|null, "length": number|null, "source_span_ids": string[] }] }
- source_spans: { "span_id": { "text": string, "page": number|string|null, "section": string|null, "start": number|null, "end": number|null } }`;
}

export async function parseProtocol(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  modelId?: string
): Promise<ProtocolParseResult> {
  const userContent: UserContent = [];
  const protocolContext = options.text ? await buildProtocolContext(options.text) : null;

  if (options.fileData) {
    userContent.push({
      type: "file",
      data: new Uint8Array(options.fileData),
      mediaType: getMimeType(options.fileName || source),
    });
  }

  let promptText = `Parse this sequencing protocol into the required JSON object.

Source: ${source}`;

  if (options.text) {
    promptText += `

Protocol content:
${options.text}${protocolContext?.prompt_block || ""}`;
  }

  userContent.push({ type: "text", text: promptText });

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: protocolJsonPrompt(),
    messages: [{ role: "user", content: userContent }],
    stopWhen: stepCountIs(5),
  });

  const finalized = await finalizeProtocolFromModel(
    text,
    protocolContext?.inventory || { candidates: [], source_spans: {} }
  );
  const protocol = ProtocolSchema.parse(finalized);
  return { protocol, raw: text };
}

export async function parseProtocolMarkdown(
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

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: systemPrompt,
    messages: [{ role: "user", content: userContent }],
    stopWhen: stepCountIs(5),
  });

  return text;
}
