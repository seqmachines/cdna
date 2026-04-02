---
name: seq-protocol-parser
description: Parse sequencing protocol documents (PDFs, text files, or web pages) to extract structured information about library preparation methods. Use this skill whenever the user asks to parse, extract, summarize, or document a sequencing protocol, library prep method, or NGS workflow. Also trigger when the user provides a 10x Genomics, Illumina, or other sequencing kit user guide and wants to understand the adapter sequences, library structure, or step-by-step molecular biology. Trigger for any request involving phrases like "extract primer sequences", "parse this protocol", "document this library prep", "what are the adapters in this kit", or when the user uploads a sequencing user guide PDF.
---

# Sequencing Protocol Parser

Parse sequencing protocol documents into a standardized structured format covering metadata, oligo sequences, step-by-step library construction, and sequencing read configuration.

## When to use this skill

Any time the user provides a sequencing protocol document (PDF, text, or URL) and wants structured extraction of how the library is built. The input could be a 10x Genomics user guide, an Illumina library prep manual, an Oxford Nanopore protocol, a published methods paper, or any document describing an NGS library preparation workflow.

## Available tools

You have access to two tools:
- **web_search** — Search Google for protocol information, supplementary materials, or papers
- **fetch_url** — Fetch and read the content of any URL found via search (PDFs are auto-converted to text)

Use these when the provided content is incomplete or doesn't contain the information you need.

If the input is just a protocol or assay name (e.g., "Drop-seq", "10x Chromium 3' v3"), use web_search to find the protocol documentation and extract the library structure.

## Input handling

The input can arrive in several forms:

- **PDF file**: Use `pdfplumber` or `pdftotext -layout` to extract text. Sequencing protocols often have important whitespace formatting (oligo diagrams, tables), so layout preservation matters. For long PDFs (>50 pages), focus on sections about "Library Construction", "Reagents", "Adapter Sequences", "Sequencing", and appendices listing oligo sequences.
- **Text/Markdown**: Read directly.
- **URL/web page**: Use `web_fetch` to retrieve the page content.

**Important**: Sequencing protocols are dense and use specialized notation. Pay close attention to:
- Oligo orientation (5'→3' vs 3'→5')
- Modified bases (rG = RNA guanosine, rGrGrG = three RNA Gs, /5Biosg/ = 5' biotin, etc.)
- Bracket notation for variable regions: `[16-bp cell barcode]`, `[UMI]`, `[sample index]`
- Poly-nucleotide tails: `(T)30`, `(A)n`
- Degenerate bases: V = A/C/G (not T), N = any base, B = C/G/T (not A)

## Output format

Produce a single Markdown document with four numbered sections. Read `references/example-output.md` for a complete worked example showing the exact structure, level of detail, and ASCII diagram style to target.

### Section 0: Metadata

Extract and present:
- **Protocol name**: The official assay or kit name (e.g., "Chromium Single Cell 3' Gene Expression v4")
- **Company/Manufacturer**: Who makes/publishes it
- **Chemistry version(s)**: Only include the version that matches the protocol document being parsed, and the latest version if different. Do NOT list older/previous kit versions or discontinued chemistries.
- **Published/Released date**: From the document metadata, cover page, or revision history
- **Document reference**: Document number, revision (e.g., "CG000731 Rev B")
- **Source URL**: If provided by the user
- **Brief description**: 1–2 sentences max. State what molecule is captured, what platform it targets, and its key differentiator. Keep it concise.
- **Processing software**: List recommended data processing tools if mentioned in the document (e.g., Cell Ranger, Space Ranger, DRAGEN, bcl2fastq)
- **Associated files**: List any reference files mentioned (e.g., barcode whitelists like "3M-february-2018.txt.gz", feature reference CSVs, genome indices)

### Section 1: Adapter and Primer Sequences

List every named oligo/adapter/primer in the protocol. For each one:

1. **Name** it clearly (e.g., "Template Switching Oligo (TSO)", "Bead oligo-dT")
2. **Show the full sequence** in 5'→3' orientation. Use monospaced formatting. Preserve any modifications (RNA bases, biotin, phosphorothioate bonds, etc.)
3. **Role**: a short functional label (e.g. "Flow cell primer", "Bead-bound oligo-dT", "Template switch oligo"). Keep it concise — 2–5 words.
4. Only include oligos for the current protocol version. Do not list oligos from older/previous chemistry versions.

For double-stranded adapters, show both strands with their relative alignment.

Organize oligos in the order they appear in the workflow (capture → RT → amplification → fragmentation → ligation → library PCR → sequencing primers → flow cell adapters).

### Section 2: Step-by-Step Library Generation

Walk through the molecular biology from initial capture/input to the final library molecule. For each step:

1. **Title**: Short descriptive name (e.g., "mRNA capture and reverse transcription")
2. **Explanation**: Plain-English description of what happens biochemically. Mention the enzyme(s) involved and the purpose of the step.
3. **ASCII diagram** (strongly recommended for every step): Show the molecular products using the notation conventions below. This is the most important part — diagrams are displayed prominently in the review UI while explanations are collapsed by default. Every step that changes the molecular structure should include a diagram.
4. **Version differences**: Note any changes across kit versions

#### ASCII diagram conventions

These conventions make diagrams consistent and readable:

```
5'- SEQUENCE -3'              Sense strand, left to right
3'- SEQUENCE -5'              Antisense strand, left to right
-------->                     Direction of polymerase extension
<--------                     Extension in reverse direction
XXXXXXXXX                     Variable/unknown sequence (cDNA, genomic, etc.)
[16-bp cell barcode]          Named variable-length region
(dT)V or (T)30               Poly-T with degenerate base
(pA)B or (A)n                Poly-A
|--5'-                        Attached to bead
*A or A*                      A-tail overhang from end-repair
...                           Sequence continues (truncated for space)
N                             Any base (used in final structure diagrams)
rG                            RNA base
```

#### Alignment rules

Correct vertical alignment of strands, primers, and labels is critical. Follow these rules strictly:

1. **Always wrap diagrams in triple backticks** (` ``` `) — never use tab-indented code blocks. Triple-backtick fencing guarantees monospace rendering and preserves space-based alignment.
2. **Use only space characters for indentation** — never use tab characters. Each character (letter, bracket, dash, parenthesis) occupies exactly one column in monospace.
3. **Never abbreviate or truncate sequences**. Write out every nucleotide of every region explicitly. Do not use `...` to shorten internal regions. The full sequence must be visible so reviewers can verify correctness.
4. **Build diagrams bottom-up**: write the longest line first (usually the full library construct), then position shorter lines (primers, arrows) by counting the exact character offset to the binding site.
5. **Count characters explicitly** when aligning a primer to a construct. If the primer binds starting at character position 42 of the construct line, the primer line must have exactly 42 leading spaces.
6. **Verify alignment** by checking 2–3 landmark nucleotides on the primer and confirming they sit directly above/below the same nucleotides on the construct strand.
7. **For label lines** (under the final library structure), center each label within the character span of its region on the sequence line above.

For steps that produce multiple product types (e.g., after fragmentation), show each product separately and note which ones are amplifiable vs. which are dead ends.

### Section 3: Library Sequencing

Describe how the final library is sequenced. For each read:

1. **Read name**: e.g., "Read 1", "Index 1 (i7)", "Index 2 (i5)", "Read 2"
2. **Sequencing primer**: Which primer initiates the read
3. **Template strand**: Top or bottom strand
4. **What is read**: The biological information obtained (cell barcode, UMI, cDNA insert, sample index, etc.)
5. **Cycle count**: How many cycles, and version-specific differences
6. **ASCII diagram** (required for every read): Show the sequencing primer annealing to the final library construct and the direction of sequencing extension

#### Library sequencing ASCII diagram format

The ascii_diagram for each sequencing read MUST show:
- The full final library construct (both strands) with all regions written out explicitly — no abbreviation
- The sequencing primer annealing to its complementary region on a separate line
- An arrow (`--------->` or `<---------`) indicating the direction of sequencing extension from the primer
- 5'→3' labels on both strands and the primer
- Use `N` for each unknown nucleotide position (e.g., 16 N's for a 16-bp barcode) — write every N, do not shorten
- Wrap the diagram in triple backticks, use spaces only (no tabs)

The primer line must be aligned so its binding site is directly above or below the corresponding sequence on the library strand. Count character positions explicitly to ensure alignment.

Example (Read 1 sequencing the cell barcode and UMI):
```
                         5'- ACACTCTTTCCCTACACGACGCTCTTCCGATCT------------------------->
3'- ...GATGTGCTGCGAGAAGGCTAGANNNNNNNNNNNNNNNNNNNNNNNNNN(pA)BXXX...XXXTCTAGCCTTCTCG... -5'
```

## Tips for tricky protocols

- **Dual-index protocols**: Some libraries have two sample indices (i7 and i5). Make sure to capture both index reads.
- **Feature barcoding / multi-modal**: If the protocol supports multiple modalities (gene expression + antibody capture + ATAC, etc.), note that in the metadata and flag where the library structures diverge.
- **Protocols with sub-libraries**: Some methods (e.g., CITE-seq, Multiome) produce multiple library types from the same cells. Parse each library type separately under a clear heading.
- **Custom/homebrew protocols**: If the document describes a non-commercial method from a paper, still apply the same structure. Cite the paper in metadata.
- **Missing sequences**: If the document doesn't provide exact oligo sequences (some vendor docs don't), note what's missing and suggest where to find them (e.g., "Sequence not provided in user guide; check the 10x Genomics Technical Note or the teichlab/scg_lib_structs resource").

## Warnings and missing information

At the end of the output, include brief notes about:
- **Extraction warnings**: Issues encountered during parsing (e.g., ambiguous diagrams, conflicting information between sections)
- **Missing information**: Critical details not found in the source document

Keep each item to one concise sentence. Only flag items that would matter to a reviewer — omit trivial or obvious gaps. Limit to 5 items max total across both categories.

## Quality checklist

Before finalizing, verify:
- [ ] Every oligo mentioned in the protocol appears in Section 1
- [ ] The step-by-step in Section 2 traces a continuous path from input molecule to final library
- [ ] The final library diagram in Section 2 matches the sequencing reads described in Section 3
- [ ] All sequence orientations are consistent (5'→3')
- [ ] Version-specific differences are clearly labeled wherever they occur
- [ ] ASCII diagrams are properly aligned in monospace

## Output file

Save the final parsed protocol as a Markdown file named after the protocol:
`/mnt/user-data/outputs/{protocol-name}-parsed.md`

Use kebab-case for the filename (e.g., `10x-chromium-3prime-v4-parsed.md`).
