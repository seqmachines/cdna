import { z } from "zod";

const NullableString = z.string().nullable();
const SourceSpanIds = z.array(z.string()).default([]);

export const SourceSpanSchema = z
  .object({
    text: z.string(),
    page: z.union([z.number(), z.string()]).nullable().optional(),
    section: NullableString.optional(),
    start: z.number().nullable().optional(),
    end: z.number().nullable().optional(),
  })
  .passthrough();

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
    source_span_ids: SourceSpanIds,
    uncertainty: NullableString.optional(),
  })
  .passthrough();

export const LibraryGenerationStepSchema = z
  .object({
    step_number: z.number().int().positive(),
    name: z.string(),
    operation: NullableString.optional(),
    inputs: z.array(z.string()).default([]),
    outputs: z.array(z.string()).default([]),
    product_structure: NullableString.optional(),
    used_sequence_names: z.array(z.string()).default([]),
    conditions: z.array(z.string()).default([]),
    source_span_ids: SourceSpanIds,
  })
  .passthrough();

export const LibrarySequencingReadSchema = z
  .object({
    read_name: z.string(),
    platform: NullableString.optional(),
    primer: NullableString.optional(),
    direction: NullableString.optional(),
    cycles: z.number().int().positive().nullable().optional(),
    template_strand: NullableString.optional(),
    what_is_read: z.array(z.string()).default([]),
    source_span_ids: SourceSpanIds,
  })
  .passthrough();

export const ReadSegmentSchema = z
  .object({
    name: z.string(),
    start: z.number().int().positive().nullable().default(null),
    end: z.number().int().positive().nullable().default(null),
    source_span_ids: SourceSpanIds,
  })
  .passthrough();

export const FinalLibrarySegmentSchema = z
  .object({
    name: z.string(),
    type: z.string(),
    sequence: NullableString.default(null),
    length: z.number().int().nonnegative().nullable().default(null),
    source_span_ids: SourceSpanIds,
  })
  .passthrough();

export const ProtocolSchema = z
  .object({
    metadata: z.record(z.string(), z.unknown()).default({}),
    adapter_primer_sequences: z.array(AdapterPrimerSequenceSchema).default([]),
    library_generation: z.array(LibraryGenerationStepSchema).default([]),
    library_sequencing: z.array(LibrarySequencingReadSchema).default([]),
    read_structure: z.record(z.string(), z.array(ReadSegmentSchema)).default({}),
    final_library_structure: z
      .object({
        orientation: NullableString.optional(),
        segments: z.array(FinalLibrarySegmentSchema).default([]),
      })
      .passthrough()
      .default({ segments: [] }),
    source_spans: z.record(z.string(), SourceSpanSchema).default({}),
    warnings: z.array(z.string()).default([]),
  })
  .strict();

export type Protocol = z.infer<typeof ProtocolSchema>;

export interface ProtocolParseResult {
  protocol: Protocol;
  raw: string;
}
