# Baseline Oligo Extraction Skill

Use this skill for one-shot extraction of adapter, primer, and oligo records from sequencing protocol text when candidate names are provided as guidance.

## Goal

Return the exhaustive benchmark record set that a careful human curator would expect: one record for every meaningful adapter, primer, oligo, sequencing primer, index primer, linker, blocking strand, or oligo family that is supported by the protocol text.

Do not stop after the first obvious oligo section. Continue through the complete protocol text and include all supported records.

A result with only one or two records is usually incomplete unless the full protocol source truly contains only one or two exact sequence-bearing adapter/primer/oligo entries.

## Candidate Names

- Treat candidate names as a vocabulary and naming guide, not as a closed whitelist.
- Prefer an exact `canonical_id` from the candidate list when the protocol clearly names the same item.
- If the protocol contains a real held-out item that is not in the candidate list, still output it. Create a concise normalized `canonical_id` from the protocol name.
- For protocol-specific oligo families, prefix the canonical ID with the protocol slug, for example `split-seq_round-2-ligation-barcodes`.
- For common adapters and primers shared across protocols, do not add the protocol prefix unless the protocol-specific modification is part of the name.
- Use candidate names as search clues. If a candidate-like name appears in the protocol text, inspect nearby lines and tables for exact sequence evidence.

## Sequence Evidence

- Copy sequence text only from the provided protocol text. Do not infer, complete, reverse-complement, or repair sequences.
- Prefer the explicit `sequence` column or the oriented sequence line over prose around it.
- Also inspect final-library/product constructs. When a construct line explicitly contains known adapter, primer, or index segments, output each supported segment as its own record if the exact bases are visible in the construct.
- Preserve modifications such as `/5Phos/`, `/5Biosg/`, `rG`, and `+G`.
- Preserve orientation when present. Default to `5_to_3` only when the source table gives a conventional oligo sequence without an explicit reverse orientation.
- If a named oligo is present but no exact bases are printed, omit it.

## Family Generalization

Many protocols list plate rows where dozens or hundreds of oligos share the same backbone and differ only by barcode, UMI, or sample index. Collapse these into one generalized family record instead of returning every row.

Use bracket placeholders for the variable segment:

- `[8-bp Round1 barcode]`
- `[8-bp Round2 barcode]`
- `[8-bp Round3 barcode]`
- `[10-bp UMI]`
- `[6-bp i7 sample index]`
- `[8-bp sample index]`
- `[random hexamer]`

Examples of families to collapse when supported by source rows:

- Round 1 oligo-dT or dtVN RT primer rows.
- Round 1 random hexamer RT primer rows.
- Round 2 ligation barcode rows.
- Indexed library PCR primer rows that differ only by i7/i5/sample index.
- Plate or well-positioned barcode rows with the same primer backbone.

Do not collapse distinct singleton oligos such as linker strands, blocking strands, template switching oligos, read sequencing primers, or separate P5/P7 primers.

## Output Requirements

- Return only JSON with a top-level `records` array.
- Each record must include `canonical_id`, `display_name`, `record_type`, `protocol_version`, `evidence`, `sequence`, and `orientation`.
- `sequence` may be either an exact source sequence or a generalized template over multiple copied source rows.
- `evidence` should include enough copied source text to justify the record. For generalized families, cite a representative source row and table/family context.
- There is no hard maximum record count. Return all supported singleton records plus all supported collapsed family records.
- Do not return hundreds of individual plate rows when they share a backbone; collapse those rows into families. But do not collapse distinct named oligos, distinct primers, distinct adapters, or distinct sequencing/index primers into one record.

## Checklist

- Did you inspect every source section, including spreadsheet-derived text appended after the PDF text?
- Did you inspect oligonucleotide sequence sections, final product/library construct sections, sample index PCR sections, and sequencing read configuration sections?
- Did you include linker, blocking, template switching, PCR primer, index primer, and sequencing primer rows, not just obvious barcode rows?
- For 10x-style documents, did you include bead/gel bead oligos, TSO if printed, cDNA/pre-amp primers if printed, sample-index/library PCR primers, P5/P7 or equivalent adapters if exact bases are visible, and sequencing primers if exact bases are visible?
- Did you scan every line containing oligo, primer, adapter/adaptor, bead, index, read 1/read 2, P5/P7, Nextera, TruSeq, TSO, cDNA, pre-amp, PCR, linker, or blocking?
- Did you collapse repeated table rows into families with placeholders?
- Did you avoid copying candidate sequences, because candidates are name-only guidance?
- Did every returned sequence or template have direct support in the protocol text?
