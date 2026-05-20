import { generateText, stepCountIs } from "ai";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loadSkill } from "./load-skill";
import { resolveModel, DEFAULT_MODEL } from "./models";
import { ProtocolSchema, type Protocol, type ProtocolParseResult } from "./protocol-schema";
import {
  buildProtocolContext,
  finalizeProtocolFromModel,
  parseAuditFromModel,
  type PythonProtocolContext,
} from "./python-protocol-tools";
import type { UserContent } from "ai";

type FinalOligo = Protocol["adapter_primer_sequences"][number];

const MIME_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".doc": "application/msword",
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xls": "application/vnd.ms-excel",
  ".xlsx":
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

function getMimeType(filename: string) {
  const ext = filename.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  return MIME_TYPES[ext] || "application/octet-stream";
}

function baseProtocolJsonPrompt() {
  return `You are cDNA, a sequencing protocol parser.

Return ONLY valid JSON matching this exact top-level object:
{
  "metadata": {
    "modality": [],
    "category": [],
    "inputs": [],
    "outputs": [],
    "cost": null,
    "time": null
  },
  "adapter_primer_sequences": [],
  "source_spans": {},
  "warnings": []
}

Rules:
- Do not return Markdown, prose, code fences, or explanations outside JSON.
- Extract only protocol metadata and adapter/primer/oligo sequences.
- metadata.modality may include: "DNA", "RNA", "protein", "chromatin_accessibility", "VDJ", "other".
- metadata.category may include: "single_cell", "spatial", "time_series", "bulk", "other".
- metadata.inputs and metadata.outputs are arrays of structured entries with type, value/min/max, unit, description, and source_span_ids.
- metadata.cost and metadata.time are nullable structured objects with source_span_ids.
- adapter_primer_sequences must contain exact copied sequences from the source only.
- Never invent adapter, primer, barcode, UMI, index, cost, time, input, or output quantities.
- Never generate, normalize, repair, complete, reverse-complement, or otherwise modify sequence strings.
- If a named sequence is mentioned but exact bases are missing, set "sequence": null and add a warning.
- Preserve modifications and placeholders when present, such as rG, /5Phos/, /5Biosg/, (T)30, degenerate bases, [16-bp cell barcode], or [8-bp sample index].
- Use source_spans to store copied evidence text. Each critical claim should cite source_span_ids.
- warnings should list missing sequences, ambiguous orientation, or unsupported claims.

Required object shapes:
- inputs[]/outputs[]: { "type": string, "value": number|string|null, "min": number|null, "max": number|null, "unit": string|null, "description": string|null, "source_span_ids": string[] }
- cost: null OR { "amount": number|string|null, "min": number|null, "max": number|null, "currency": string|null, "description": string|null, "source_span_ids": string[] }
- time: null OR { "duration": number|string|null, "min": number|null, "max": number|null, "unit": string|null, "description": string|null, "source_span_ids": string[] }
- adapter_primer_sequences[]: { "name": string, "role": string|null, "sequence": string|null, "orientation": "5_to_3"|"3_to_5"|"unknown"|null, "modifications": string[], "source": "known_inventory"|"deterministic"|"regex"|"llm_named_missing"|null, "inventory_id": string|null, "source_span_ids": string[], "uncertainty": string|null }
- source_spans: { "span_id": { "text": string, "page": number|string|null, "section": string|null, "start": number|null, "end": number|null } }`;
}

function metadataOnlyJsonPrompt() {
  return `${baseProtocolJsonPrompt()}

For this call, extract metadata only:
- Return adapter_primer_sequences as [].
- Do not add oligo rows. Oligo rows are supplied by the deterministic extractor.`;
}

function hasAuditFindings(audit: Record<string, unknown>) {
  const status = audit.audit_status;
  const hasListFindings = [
    "missing_sequences",
    "candidate_reviews",
    "suspected_regex_gaps",
    "proposed_inventory_rows",
    "proposed_extractor_changes",
  ].some((key) => Array.isArray(audit[key]) && audit[key].length > 0);
  return status !== "pass" || hasListFindings;
}

function userContentForSource(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  promptPrefix: string
): UserContent {
  const userContent: UserContent = [];

  if (options.fileData) {
    userContent.push({
      type: "file",
      data: new Uint8Array(options.fileData),
      mediaType: getMimeType(options.fileName || source),
    });
  }

  let promptText = `${promptPrefix}

Source: ${source}`;

  if (options.text) {
    promptText += `

Protocol content:
${options.text}`;
  }

  userContent.push({ type: "text", text: promptText });
  return userContent;
}

function candidateString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function candidateStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function candidateOrientation(value: unknown): FinalOligo["orientation"] {
  return value === "5_to_3" || value === "3_to_5" || value === "unknown" ? value : "unknown";
}

function candidateNumber(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}

function candidateDecision(value: unknown) {
  return value === "accept" || value === "reject" || value === "review" ? value : null;
}

function outputSlug(source: string) {
  let value = source;
  try {
    const url = new URL(source);
    value = url.pathname.split("/").filter(Boolean).pop() || url.hostname;
  } catch {
    value = path.basename(source);
  }

  value = value.replace(/\.[A-Za-z0-9]+$/, "");
  value = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return value || "protocol";
}

function tsvCell(value: unknown) {
  if (value == null) return "";
  if (Array.isArray(value)) return value.join(";");
  return String(value).replace(/\t/g, " ").replace(/\r?\n/g, " ").trim();
}

function sequenceInventoryTsv(inventory: PythonProtocolContext["inventory"]) {
  const columns: Array<{ header: string; key: string }> = [
    { header: "id", key: "id" },
    { header: "source", key: "source" },
    { header: "inventory_id", key: "inventory_id" },
    { header: "inventory_file", key: "inventory_file" },
    { header: "name_hint", key: "name_hint" },
    { header: "role_hint", key: "role_hint" },
    { header: "sequence", key: "sequence" },
    { header: "orientation_hint", key: "orientation_hint" },
    { header: "modifications", key: "modifications" },
    { header: "source_span_id", key: "source_span_id" },
    { header: "heuristic_score", key: "confidence" },
    { header: "start", key: "start" },
    { header: "end", key: "end" },
    { header: "platform", key: "platform" },
    { header: "protocol", key: "protocol" },
    { header: "source_url", key: "source_url" },
    { header: "notes", key: "notes" },
    { header: "source_text", key: "source_text" },
  ];

  const rows = (inventory.candidates || []).map((candidate) =>
    columns.map((column) => tsvCell(candidate[column.key])).join("\t")
  );
  return [columns.map((column) => column.header).join("\t"), ...rows].join("\n") + "\n";
}

function finalOligoTsv(protocol: Protocol) {
  const columns = [
    "name",
    "role",
    "sequence",
    "orientation",
    "modifications",
    "source",
    "inventory_id",
    "source_span_ids",
    "confidence",
    "review_status",
    "review_note",
    "uncertainty",
  ];
  const rows = protocol.adapter_primer_sequences.map((oligo) =>
    columns.map((column) => tsvCell(oligo[column as keyof typeof oligo])).join("\t")
  );
  return [columns.join("\t"), ...rows].join("\n") + "\n";
}

async function writeExtractionArtifacts(
  source: string,
  result: Omit<ProtocolParseResult, "artifacts">,
  inventory: PythonProtocolContext["inventory"],
  protocolText?: string
) {
  const slug = outputSlug(source);
  const rootDir = process.cwd();
  const outputDir = path.join(/* turbopackIgnore: true */ rootDir, "outputs");
  const finalJsonRelative = path.join("outputs", `${slug}.extract.json`);
  const finalOligoTsvRelative = path.join("outputs", `${slug}.final-oligos.tsv`);
  const inventoryTsvRelative = path.join("outputs", `${slug}.sequence-inventory.tsv`);
  const protocolTextRelative = protocolText
    ? path.join("outputs", `${slug}.protocol.txt`)
    : undefined;
  const artifacts = {
    final_json: finalJsonRelative,
    final_oligo_tsv: finalOligoTsvRelative,
    sequence_inventory_tsv: inventoryTsvRelative,
    ...(protocolTextRelative ? { protocol_text: protocolTextRelative } : {}),
  };
  const resultWithArtifacts: ProtocolParseResult = { ...result, artifacts };

  await mkdir(outputDir, { recursive: true });
  if (protocolTextRelative && protocolText !== undefined) {
    await writeFile(
      path.join(/* turbopackIgnore: true */ rootDir, protocolTextRelative),
      protocolText,
      "utf-8"
    );
  }
  await writeFile(
    path.join(/* turbopackIgnore: true */ rootDir, inventoryTsvRelative),
    sequenceInventoryTsv(inventory),
    "utf-8"
  );
  await writeFile(
    path.join(/* turbopackIgnore: true */ rootDir, finalOligoTsvRelative),
    finalOligoTsv(result.protocol),
    "utf-8"
  );
  await writeFile(
    path.join(/* turbopackIgnore: true */ rootDir, finalJsonRelative),
    JSON.stringify(resultWithArtifacts, null, 2) + "\n",
    "utf-8"
  );

  return resultWithArtifacts;
}

interface CandidateReview {
  decision: "accept" | "reject" | "review" | null;
  confidence: number | null;
  suggestedName: string | null;
  suggestedRole: string | null;
  reason: string | null;
}

function auditReviewMap(audit?: Record<string, unknown>) {
  const reviews = Array.isArray(audit?.candidate_reviews) ? audit.candidate_reviews : [];
  const map = new Map<string, CandidateReview>();
  const warnings: string[] = [];
  for (const item of reviews) {
    if (!item || typeof item !== "object") continue;
    const review = item as Record<string, unknown>;
    if (candidateString(review.sequence)) {
      warnings.push(
        `Ignored LLM-proposed sequence in candidate review for ${
          candidateString(review.candidate_id) || candidateString(review.source_span_id) || "unknown candidate"
        }.`
      );
    }
    const parsed: CandidateReview = {
      decision: candidateDecision(review.decision),
      confidence: candidateNumber(review.confidence),
      suggestedName: candidateString(review.suggested_name),
      suggestedRole: candidateString(review.suggested_role),
      reason: candidateString(review.reason),
    };
    for (const key of [candidateString(review.candidate_id), candidateString(review.source_span_id)]) {
      if (key) map.set(key, parsed);
    }
  }
  return { map, warnings };
}

function strippedSequenceLetters(sequence: string) {
  return sequence
    .replace(/\[[^\]]+\]/g, "")
    .replace(/N\d+/gi, "")
    .replace(/r[ACGTU]/g, "")
    .replace(/[^A-Za-z]/g, "");
}

function hasUnexpectedLowercase(sequence: string) {
  const withoutPlaceholders = sequence.replace(/\[[^\]]+\]/g, "");
  const withoutRnaMods = withoutPlaceholders.replace(/r[ACGTU]/g, "");
  return /[a-z]/.test(withoutRnaMods);
}

function hardRejectCandidate(candidate: Record<string, unknown>, llmAccepted = false) {
  const source = candidateString(candidate.source);
  if (source === "known_inventory") return null;

  const sequence = candidateString(candidate.sequence) || "";
  const letters = strippedSequenceLetters(sequence);
  const sourceText = candidateString(candidate.source_text) || "";
  const baseLength = letters.length;
  const hasPlaceholder = /\[[^\]]+\]|N\d+/i.test(sequence);
  const hasOrientation = candidateOrientation(candidate.orientation_hint) !== "unknown";
  const hasStrongBases = /[ACGT]{8,}/.test(letters);
  const hasLabel = /\b(adapter|adaptor|primer|oligo|index|read\s*[12]|truseq|p5|p7|tso|bead|barcode)\b/i.test(
    sourceText
  );

  if (hasUnexpectedLowercase(sequence)) {
    return "Rejected: lowercase English-like text matched the permissive IUPAC fallback.";
  }
  if (baseLength < 10 && !hasPlaceholder) {
    return "Rejected: candidate is too short without an explicit variable-region placeholder.";
  }
  if (!hasStrongBases && !hasPlaceholder) {
    return "Rejected: candidate lacks a convincing A/C/G/T sequence core.";
  }
  if (!llmAccepted && source === "regex" && !hasOrientation && !hasLabel && !hasPlaceholder) {
    return "Rejected: regex fallback hit has no strong oligo context.";
  }
  return null;
}

function cleanCandidateName(name: string | null, index: number) {
  if (!name) return `Unlabeled oligo candidate ${index + 1}`;
  const trimmed = name.trim();
  if (/^[35]['’′]?$/.test(trimmed)) return `Unlabeled oligo candidate ${index + 1}`;
  if (trimmed.length > 100 && /[ACGT]{12,}/.test(trimmed)) {
    return `Unlabeled oligo candidate ${index + 1}`;
  }
  return trimmed;
}

function inventoryToAdapterPrimerSequences(
  inventory: PythonProtocolContext["inventory"],
  audit?: Record<string, unknown>
): { oligos: FinalOligo[]; warnings: string[] } {
  const { map: reviews, warnings } = auditReviewMap(audit);
  const rawOligos = (inventory.candidates || []).flatMap((candidate, index) => {
    const rawSource = candidateString(candidate.source);
    const source: FinalOligo["source"] =
      rawSource === "known_inventory" ? "known_inventory" : "deterministic";
    const candidateId = candidateString(candidate.id);
    const spanId = candidateString(candidate.source_span_id);
    const review = (candidateId && reviews.get(candidateId)) || (spanId && reviews.get(spanId)) || null;
    const hardRejectReason = hardRejectCandidate(candidate, review?.decision === "accept");
    if (hardRejectReason) return [];
    if (review?.decision === "reject") return [];

    const reviewStatus: FinalOligo["review_status"] =
      review?.decision === "accept"
        ? "accepted"
        : review?.decision === "review"
          ? "needs_review"
          : "accepted_by_rules";
    const reviewNote =
      review?.reason ||
      (reviewStatus === "accepted_by_rules"
        ? "Passed deterministic final-output filters; no LLM candidate confidence was available."
        : null);

    return {
      name:
        cleanCandidateName(review?.suggestedName || candidateString(candidate.name_hint), index) ||
        candidateString(candidate.inventory_id) ||
        `Oligo sequence ${index + 1}`,
      role: review?.suggestedRole || candidateString(candidate.role_hint),
      sequence: candidateString(candidate.sequence),
      orientation: candidateOrientation(candidate.orientation_hint),
      modifications: candidateStringArray(candidate.modifications),
      source,
      inventory_id: candidateString(candidate.inventory_id),
      source_span_ids: [spanId].filter((item): item is string => Boolean(item)),
      confidence: review?.confidence ?? null,
      review_status: reviewStatus,
      review_note: reviewNote,
      uncertainty:
        reviewStatus === "needs_review"
          ? "LLM audit marked this candidate for review."
          : rawSource === "regex" && !review
            ? "Detected by deterministic sequence-pattern fallback; verify name and role."
            : null,
    };
  });
  const oligos = dedupeFinalOligos(rawOligos);
  return { oligos, warnings };
}

function dedupeFinalOligos(oligos: FinalOligo[]) {
  const bySequence = new Map<string, FinalOligo>();
  for (const oligo of oligos) {
    const key = [
      oligo.sequence || "",
      oligo.role || "",
      oligo.orientation || "",
      oligo.inventory_id || "",
    ].join("|");
    const current = bySequence.get(key);
    if (!current) {
      bySequence.set(key, { ...oligo });
      continue;
    }

    current.source_span_ids = Array.from(
      new Set([...current.source_span_ids, ...oligo.source_span_ids])
    );
    if (!current.confidence || (oligo.confidence && oligo.confidence > current.confidence)) {
      current.confidence = oligo.confidence;
    }
    if (current.review_status !== "accepted" && oligo.review_status === "accepted") {
      current.review_status = oligo.review_status;
      current.review_note = oligo.review_note;
    }
  }
  return Array.from(bySequence.values());
}

async function runAudit(
  protocolContext: PythonProtocolContext,
  modelId?: string
): Promise<Record<string, unknown> | undefined> {
  if (!protocolContext.audit_prompt) {
    return undefined;
  }

  try {
    const auditResult = await generateText({
      model: resolveModel(modelId || DEFAULT_MODEL),
      system:
        "You are a strict sequencing protocol sequence-inventory auditor. Return only valid JSON. Do not mutate files or propose changes as if they were already applied. Never generate, rewrite, normalize, repair, complete, reverse-complement, or otherwise modify sequence strings.",
      messages: [{ role: "user", content: [{ type: "text", text: protocolContext.audit_prompt }] }],
      stopWhen: stepCountIs(2),
    });
    const parsedAudit = await parseAuditFromModel(auditResult.text);
    return hasAuditFindings(parsedAudit) ? parsedAudit : undefined;
  } catch (error) {
    return {
      audit_status: "uncertain",
      missing_sequences: [],
      suspected_regex_gaps: [],
      proposed_inventory_rows: [],
      proposed_extractor_changes: [],
      human_review_required: true,
      error: error instanceof Error ? error.message : "Audit failed",
    };
  }
}

export async function extractProtocolOnePassBaseline(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  modelId?: string
): Promise<ProtocolParseResult> {
  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: baseProtocolJsonPrompt(),
    messages: [
      {
        role: "user",
        content: userContentForSource(
          source,
          options,
          "Extract protocol metadata and adapter/primer sequences into the required JSON object."
        ),
      },
    ],
    stopWhen: stepCountIs(5),
  });

  const finalized = await finalizeProtocolFromModel(text, { candidates: [], source_spans: {} });
  const protocol = ProtocolSchema.parse(finalized);
  return { protocol, raw: text };
}

export async function extractProtocolStaged(
  source: string,
  options: { fileData?: Buffer; fileName?: string; text?: string },
  modelId?: string
): Promise<ProtocolParseResult> {
  if (!options.text) {
    const fallback = await extractProtocolOnePassBaseline(source, options, modelId);
    fallback.protocol.warnings.push(
      "Deterministic oligo extraction requires text or PDF-derived text; used one-pass baseline for this input."
    );
    return fallback;
  }

  const protocolContext = await buildProtocolContext(options.text);
  const audit = await runAudit(protocolContext, modelId);

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: metadataOnlyJsonPrompt(),
    messages: [
      {
        role: "user",
        content: userContentForSource(
          source,
          { text: options.text },
          "Extract protocol metadata into the required JSON object."
        ),
      },
    ],
    stopWhen: stepCountIs(3),
  });

  const metadataOnly = await finalizeProtocolFromModel(text, {
    candidates: [],
    source_spans: {},
  });
  const oligoResult = inventoryToAdapterPrimerSequences(protocolContext.inventory, audit);
  const protocol: Protocol = ProtocolSchema.parse({
    metadata: metadataOnly.metadata || {},
    adapter_primer_sequences: oligoResult.oligos,
    source_spans: {
      ...(protocolContext.inventory.source_spans || {}),
      ...((metadataOnly.source_spans as Record<string, unknown> | undefined) || {}),
    },
    warnings: [
      ...(Array.isArray(metadataOnly.warnings) ? metadataOnly.warnings : []),
      ...oligoResult.warnings,
    ],
  });

  const result = audit ? { protocol, raw: text, audit } : { protocol, raw: text };
  return writeExtractionArtifacts(source, result, protocolContext.inventory, options.text);
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

  const userContent = userContentForSource(
    source,
    options,
    "Parse this sequencing protocol and extract the complete library structure."
  );

  const { text } = await generateText({
    model: resolveModel(modelId || DEFAULT_MODEL),
    system: systemPrompt,
    messages: [{ role: "user", content: userContent }],
    stopWhen: stepCountIs(5),
  });

  return text;
}
