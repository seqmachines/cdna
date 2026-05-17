import { z } from "zod";

const NullableString = z.string().nullable();
const SourceSpanIds = z.array(z.string()).default([]);
const NullableNumber = z.number().nullable();

export const SourceSpanSchema = z
  .object({
    text: z.string(),
    page: z.union([z.number(), z.string()]).nullable().optional(),
    section: NullableString.optional(),
    start: z.number().nullable().optional(),
    end: z.number().nullable().optional(),
  })
  .passthrough();

export const MetadataQuantitySchema = z
  .object({
    type: z.string(),
    value: z.union([z.number(), z.string()]).nullable().optional(),
    min: NullableNumber.optional(),
    max: NullableNumber.optional(),
    unit: NullableString.optional(),
    description: NullableString.optional(),
    source_span_ids: SourceSpanIds,
  })
  .passthrough();

export const CostSchema = z
  .object({
    amount: z.union([z.number(), z.string()]).nullable().optional(),
    min: NullableNumber.optional(),
    max: NullableNumber.optional(),
    currency: NullableString.optional(),
    description: NullableString.optional(),
    source_span_ids: SourceSpanIds,
  })
  .passthrough()
  .nullable()
  .default(null);

export const TimeSchema = z
  .object({
    duration: z.union([z.number(), z.string()]).nullable().optional(),
    min: NullableNumber.optional(),
    max: NullableNumber.optional(),
    unit: NullableString.optional(),
    description: NullableString.optional(),
    source_span_ids: SourceSpanIds,
  })
  .passthrough()
  .nullable()
  .default(null);

export const ProtocolMetadataSchema = z
  .object({
    modality: z
      .array(z.enum(["DNA", "RNA", "protein", "chromatin_accessibility", "VDJ", "other"]))
      .default([]),
    category: z
      .array(z.enum(["single_cell", "spatial", "time_series", "bulk", "other"]))
      .default([]),
    inputs: z.array(MetadataQuantitySchema).default([]),
    outputs: z.array(MetadataQuantitySchema).default([]),
    cost: CostSchema,
    time: TimeSchema,
  })
  .passthrough()
  .default({
    modality: [],
    category: [],
    inputs: [],
    outputs: [],
    cost: null,
    time: null,
  });

export const AdapterPrimerSequenceSchema = z
  .object({
    name: z.string(),
    role: NullableString.optional(),
    sequence: NullableString.default(null),
    orientation: z
      .enum(["5_to_3", "3_to_5", "unknown"])
      .nullable()
      .optional(),
    modifications: z.array(z.string()).default([]),
    source: z
      .enum(["known_inventory", "deterministic", "regex", "llm_named_missing"])
      .nullable()
      .default(null),
    inventory_id: NullableString.default(null),
    source_span_ids: SourceSpanIds,
    uncertainty: NullableString.optional(),
  })
  .passthrough();

export const ProtocolSchema = z
  .object({
    metadata: ProtocolMetadataSchema,
    adapter_primer_sequences: z.array(AdapterPrimerSequenceSchema).default([]),
    source_spans: z.record(z.string(), SourceSpanSchema).default({}),
    warnings: z.array(z.string()).default([]),
  })
  .strict();

export type Protocol = z.infer<typeof ProtocolSchema>;

export interface ProtocolParseResult {
  protocol: Protocol;
  raw: string;
  audit?: Record<string, unknown>;
  artifacts?: {
    final_json?: string;
    sequence_inventory_tsv?: string;
    protocol_text?: string;
  };
}
