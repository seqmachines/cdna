# Baseline Oligo Extraction Skill

Use this skill for one-shot extraction of adapter, primer, and oligo records from sequencing protocol text. The baseline is allowed to read the complete provided input, but it must follow the same sequence-safety contract as the current evidence-first pipeline.

## Goal

Return an exhaustive `ProtocolOligoSet` that a careful human curator would expect: one object for every meaningful adapter, primer, oligo, sequencing primer, index primer, linker, blocking strand, hairpin, strand-pair adapter, or oligo family that is supported by the protocol text.

Exhaustiveness is required. Do not stop after the first obvious oligo section. Continue through the complete protocol text and include all supported records.

A result with only one or two records is usually incomplete unless the full protocol source truly contains only one or two exact sequence-bearing adapter/primer/oligo entries.

## Core Contract

1. Never invent an oligo, adapter, or primer sequence.
2. Only use sequences from deterministic candidate extraction, explicit source evidence, or approved memory.
3. If the protocol names an oligo but does not show the sequence, output it with `sequence: null` and `sequence_source: not_shown_in_protocol`, unless approved memory is provided.
4. Every extracted oligo should include source evidence when available.
5. Do not silently reverse-complement sequences.
6. Do not merge protocol versions unless evidence says they are identical.
7. Prefer high recall. Ambiguous items should be included with notes, not dropped.
8. If memory is used, set `sequence_source: memory_completed` and include `memory_id`.

## Naming Rules

1. Use cleaned canonical names.
2. Preserve source names as aliases when they differ.
3. Prefer standard names when sequence identity is clear:
   - Illumina P5 adapter
   - Illumina P7 adapter
   - Illumina TruSeq Read 1 primer
   - Illumina TruSeq Read 2 primer
   - Illumina Nextera Read 1 primer
   - Illumina Nextera Read 2 primer
4. Use `TruSeq sample index sequencing primer forward`, not `TruSeq sample index sequencing primer / adapter forward`.
5. If the same sequence appears as a sequencing primer and as one strand of an adapter, keep both concepts when evidence supports both.
6. Do not collapse cDNA forward and reverse primers into one object.
7. Collapse true strand-pair adapters into one `double_stranded` object with components.
8. If one name maps to different real sequences, create stable variants.

Candidate names, when provided, are a vocabulary and naming guide, not a closed whitelist. Prefer exact candidate IDs when they clearly match, but still output real protocol-supported held-out items.

## Role Rules

Use coarse roles only:

- `primer`
- `adapter`
- `oligo`
- `primer_site`
- `tn5_binding_site`
- `probe`
- `promoter`
- `unknown`

Map specific terms into coarse roles:

- sequencing_primer, index_primer, pcr_primer, rt_primer, random_primer -> `primer`
- bead_oligo, barcode_oligo, blocking_oligo, template_switching_oligo, linker_oligo, ligation_oligo, splint_oligo -> `oligo`

Use `unknown` only when name and context give no useful role signal.

## Kind Rules

Use:

- `single`: one normal oligo sequence.
- `assembled`: sequence contains meaningful subparts such as barcode, UMI, index, polyT, or another known oligo sequence.
- `double_stranded`: two strands belong to one adapter/adaptor object.
- `hairpin`: explicit hairpin adapter or hairpin structure.

Examples:

- Beads-oligo-dT -> assembled
- TruSeq adapter -> double_stranded
- NEB Hairpin adapter -> hairpin
- cDNA forward primer -> single
- cDNA reverse primer -> single

Many protocols list plate rows where dozens or hundreds of oligos share the same backbone and differ only by barcode, UMI, or sample index. Collapse these into one generalized family record instead of returning every row.

There is no hard maximum record count. Return all supported singleton records plus all supported collapsed family records.

## Component Rules

Put substructure inside `components`.

Common component roles:

- cell_barcode
- barcode
- umi
- sample_index
- polyT
- primer
- adapter
- forward_strand
- reverse_strand
- top_strand
- bottom_strand
- upper_strand
- lower_strand
- modification

Containment rule: if oligo A contains the full sequence of oligo B and B is at least 5 nt, add B as a component of A when supported by known memory or source context.

For repeated family rows, use bracket placeholders for variable segments, such as:

- `[8-bp Round1 barcode]`
- `[8-bp Round2 barcode]`
- `[8-bp Round3 barcode]`
- `[10-bp UMI]`
- `[6-bp i7 sample index]`
- `[8-bp sample index]`
- `[random hexamer]`

Do not collapse distinct singleton oligos such as linker strands, blocking strands, template switching oligos, read sequencing primers, or separate P5/P7 primers.

## Sequence Rules

1. Keep sequences expanded for now.
2. Preserve biological placeholders:
   - `[16-bp cell barcode]`
   - `[10-bp UMI]`
   - `[8-bp sample index]`
   - `[6-bp index]`
3. Preserve modification notation:
   - `/5Phos/`
   - `/5Bio/`
   - `/3InvdT/`
   - `/3SpC3/`
   - `/5rApp/`
   - `/ddU/`
4. Normalize obvious formatting only:
   - remove outer `5'-` and `-3'`
   - remove alignment spaces
   - normalize `/phos/` to `/5Phos/` only when clearly 5-prime phosphate
5. Preserve chemistry markers:
   - `rG`, `rA`, `rU`, `rC`
   - `*`
   - `+`
   - `(dU)`
6. Do not reverse-complement during normalization.

If a named oligo is present but exact bases are absent, keep it with `sequence: null` and `sequence_source: not_shown_in_protocol`.

## Output Rules

Return only JSON for a `ProtocolOligoSet`:

```json
{
  "protocol_id": "...",
  "protocol_name": "...",
  "split": "train",
  "source_files": [],
  "oligos": [
    {
      "protocol_id": "...",
      "protocol_name": "...",
      "oligo_id": "...",
      "name": "...",
      "aliases": [],
      "role": "primer|adapter|oligo|primer_site|tn5_binding_site|probe|promoter|unknown",
      "kind": "single|assembled|double_stranded|hairpin",
      "sequence": "... or null",
      "direction": "5_to_3|3_to_5|unknown",
      "components": [],
      "sequence_source": "explicit_in_protocol|explicit_in_linked_table|memory_completed|curated_ground_truth|not_shown_in_protocol|unknown",
      "memory_id": null,
      "evidence": [],
      "notes": null
    }
  ],
  "notes": null
}
```

Each evidence item should use:

```json
{
  "source_id": null,
  "page": null,
  "section": null,
  "quote": "exact copied source quote"
}
```

## Human Review Flags

Flag for review in `notes` when:

1. Sequence is missing but name is present.
2. Same name has multiple different sequences.
3. Same sequence has very different names.
4. Direction is unclear.
5. Strand pair is only partially reverse-complementary.
6. Protocol version may change barcode/UMI length.
7. A sequence appears only in a diagram, not text/table.
8. The item may be a primer site/binding site rather than an actual ordered oligo.
9. The LLM wants to use memory to fill a missing sequence.

## Final Checklist

- Did you inspect every source section, including spreadsheet-derived text appended after the PDF text?
- Did you inspect oligonucleotide sequence sections, final product/library construct sections, sample index PCR sections, and sequencing read configuration sections?
- Did you include linker, blocking, template switching, PCR primer, index primer, and sequencing primer rows, not just obvious barcode rows?
- For 10x-style documents, did you include bead/gel bead oligos, TSO if printed, cDNA/pre-amp primers if printed, sample-index/library PCR primers, P5/P7 or equivalent adapters if exact bases are visible, and sequencing primers if exact bases are visible?
- Did you scan every line containing oligo, primer, adapter/adaptor, bead, index, read 1/read 2, P5/P7, Nextera, TruSeq, TSO, cDNA, pre-amp, PCR, linker, or blocking?
- Did you collapse repeated table rows into families with placeholders?
- Did every returned sequence or template have direct support in the protocol text?
