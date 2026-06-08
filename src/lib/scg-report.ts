import { generateText, stepCountIs } from "ai";
import { extractText, getDocumentProxy } from "unpdf";
import { loadSkill } from "./load-skill";
import { DEFAULT_MODEL, resolveModel } from "./models";
import { isPdfInput, isTextInput, type PreparedProtocolInput } from "./extract-input-text";

export interface ScgReportMetadata {
  protocol_name: string | null;
  published_date: string | null;
  company: string | null;
  document_reference: string | null;
  source_url: string | null;
  brief_description: string | null;
}

export interface ScgReportResult {
  mode: "scg_report";
  metadata: ScgReportMetadata;
  sections: {
    metadata: string;
    adapter_primer_sequences: string;
    library_generation: string;
    library_sequencing: string;
  };
  report_markdown: string;
  raw: string;
  warnings: string[];
}

const EMPTY_METADATA: ScgReportMetadata = {
  protocol_name: null,
  published_date: null,
  company: null,
  document_reference: null,
  source_url: null,
  brief_description: null,
};

function scgReportPrompt() {
  const { skillMd, exampleMd } = loadSkill();
  return `${skillMd}

## Demo constraints

Return only the complete Markdown report. Do not include a filename, save-path instruction, preamble, or commentary outside the report.
Use these top-level sections exactly:
- Metadata
- 1. Adapter and Primer Sequences
- 2. Step-by-Step Library Generation
- 3. Library Sequencing

If a field is not found in the source, write "Not found" for that field instead of inventing it.

## Reference example

${exampleMd}`;
}

function reportUserPrompt(source: string, input: PreparedProtocolInput) {
  const content = input.text || "";
  return `Source: ${source}

Parse this protocol into a scg_lib_structs-style library-structure report.

Protocol content:
${content}`;
}

function cleanMarkdown(text: string) {
  return text
    .replace(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$/i, "$1")
    .trim();
}

function headingTitle(line: string) {
  const match = line.match(/^#{1,6}\s+(.+?)\s*$/);
  return match ? match[1].trim() : null;
}

function sectionKeyForHeading(title: string) {
  const normalized = title
    .toLowerCase()
    .replace(/^\d+\.\s*/, "")
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (normalized === "metadata") return "metadata";
  if (normalized.includes("adapter") && normalized.includes("primer")) {
    return "adapter_primer_sequences";
  }
  if (normalized.includes("step by step") || normalized.includes("library generation")) {
    return "library_generation";
  }
  if (normalized.includes("library sequencing")) return "library_sequencing";
  return null;
}

function extractSections(markdown: string): ScgReportResult["sections"] {
  const sections: ScgReportResult["sections"] = {
    metadata: "",
    adapter_primer_sequences: "",
    library_generation: "",
    library_sequencing: "",
  };
  const lines = markdown.split(/\r?\n/);
  let current: keyof ScgReportResult["sections"] | null = null;
  let buffer: string[] = [];

  function flush() {
    if (!current) return;
    sections[current] = buffer.join("\n").trim();
  }

  for (const line of lines) {
    const title = headingTitle(line);
    const next = title ? sectionKeyForHeading(title) : null;
    if (next) {
      flush();
      current = next;
      buffer = [line];
      continue;
    }
    if (current) buffer.push(line);
  }

  flush();
  return sections;
}

function metadataKey(label: string) {
  const normalized = label.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  if (normalized === "protocol name") return "protocol_name";
  if (normalized === "company" || normalized === "company manufacturer" || normalized === "manufacturer") {
    return "company";
  }
  if (
    normalized === "published released" ||
    normalized === "published released date" ||
    normalized === "published date" ||
    normalized === "released date"
  ) {
    return "published_date";
  }
  if (normalized === "document reference" || normalized === "document") return "document_reference";
  if (normalized === "source url" || normalized === "source") return "source_url";
  if (normalized === "brief description" || normalized === "description") return "brief_description";
  return null;
}

function nonEmptyValue(value: string | undefined) {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed || /^not found$/i.test(trimmed)) return null;
  return trimmed;
}

function extractMetadata(markdown: string, metadataSection: string): ScgReportMetadata {
  const metadata: ScgReportMetadata = { ...EMPTY_METADATA };
  const firstHeading = markdown.match(/^#\s+(.+)$/m)?.[1]?.trim();

  for (const line of metadataSection.split(/\r?\n/)) {
    const match =
      line.match(/^\s*(?:[-*]\s*)?\*\*([^*]+)\*\*:\s*(.+?)\s*$/) ||
      line.match(/^\s*(?:[-*]\s*)?([^:]+):\s*(.+?)\s*$/);
    if (!match) continue;

    const key = metadataKey(match[1]);
    if (!key) continue;
    metadata[key] = nonEmptyValue(match[2]);
  }

  if (!metadata.protocol_name && firstHeading && !sectionKeyForHeading(firstHeading)) {
    metadata.protocol_name = firstHeading;
  }

  return metadata;
}

function reportWarnings(metadata: ScgReportMetadata, sections: ScgReportResult["sections"]) {
  const warnings: string[] = [];
  if (!metadata.protocol_name) warnings.push("Protocol name was not found in the generated report.");
  if (!metadata.published_date) warnings.push("Published or released date was not found in the generated report.");
  if (!sections.adapter_primer_sequences) warnings.push("Adapter and primer sequence section was not generated.");
  if (!sections.library_generation) warnings.push("Library generation section was not generated.");
  if (!sections.library_sequencing) warnings.push("Library sequencing section was not generated.");
  return warnings;
}

export async function extractPdfTextServerless(buffer: Buffer) {
  const data = new Uint8Array(buffer);
  const pdf = await getDocumentProxy(data);
  const result = await extractText(pdf, { mergePages: false });
  const pages = Array.isArray(result.text) ? result.text : [result.text];

  return pages
    .map((pageText, index) => `[[PAGE ${index + 1}]]\n${pageText || ""}`)
    .join("\n\n")
    .trim();
}

export async function prepareScgReportInput(
  buffer: Buffer,
  fileName: string,
  contentType = ""
): Promise<PreparedProtocolInput> {
  if (isPdfInput(fileName, contentType)) {
    return { text: await extractPdfTextServerless(buffer) };
  }

  if (isTextInput(fileName, contentType)) {
    return { text: new TextDecoder().decode(buffer) };
  }

  throw new Error("The demo report parser accepts PDF or text files.");
}

export async function extractProtocolScgReport(
  source: string,
  input: PreparedProtocolInput,
  modelId?: string
): Promise<ScgReportResult> {
  if (!input.text?.trim()) {
    throw new Error("No protocol text could be extracted from this input.");
  }

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: scgReportPrompt(),
    prompt: reportUserPrompt(source, input),
    stopWhen: stepCountIs(5),
  });

  const report_markdown = cleanMarkdown(text);
  const sections = extractSections(report_markdown);
  const metadata = extractMetadata(report_markdown, sections.metadata);

  return {
    mode: "scg_report",
    metadata,
    sections,
    report_markdown,
    raw: text,
    warnings: reportWarnings(metadata, sections),
  };
}
