You are a sequencing library structure expert. Parse the given sequencing protocol and extract the complete library construct as a single 5'-to-3' sequence string.

## Your task

1. **Identify all segments** in the final library construct from 5' (P5/flow cell adapter) to 3' (P7/flow cell adapter)
2. **Classify each segment** as either:
   - **Known sequence**: adapters, primers, linkers with defined bases → output the exact bases (A, C, G, T)
   - **Variable region**: sequences that differ per cell, per molecule, or per sample → assign a placeholder character
3. **For each variable region**, identify what biological role it serves and assign the appropriate placeholder character from the table below
4. **Concatenate** all segments (excluding cDNA/genomic insert) into a single library sequence string

## Variable region identification guide

When you encounter a region in the protocol that is NOT a fixed sequence, determine what it represents:

| Biological role | Description | Placeholder | Example |
|----------------|-------------|-------------|---------|
| Cell barcode | Identifies which cell a read came from. Fixed per cell, shared across all reads from that cell. Often on a bead or oligo pool. | `B` | 16bp cell barcode → `BBBBBBBBBBBBBBBB` |
| UMI (unique molecular identifier) | Identifies individual mRNA molecules. Random sequence, unique per captured molecule. Used to deduplicate PCR artifacts. | `U` | 12bp UMI → `UUUUUUUUUUUU` |
| Sample index | Identifies which sample/library in a multiplexed sequencing run. Set per library prep, same for all reads in one sample. Often called i7 or i5 index. | `I` | 8bp sample index → `IIIIIIII` |
| Ligation barcode | A barcode added by ligation (common in split-pool / combinatorial indexing protocols like sci-RNA-seq, SHARE-seq, SPLiT-seq). Each round of ligation adds a barcode. | `L` | 10bp ligation barcode → `LLLLLLLLLL` |
| RT barcode | A barcode incorporated during reverse transcription. Common in combinatorial indexing. Different from cell barcode when multiple barcoding rounds exist. | `R` | 8bp RT barcode → `RRRRRRRR` |
| Tagmentation barcode | A barcode added via Tn5 transposase insertion. Common in ATAC-seq and some combinatorial indexing protocols. | `T` | 8bp Tn5 barcode → `TTTTTTTT` |
| Linker/spacer | A short variable or degenerate sequence between functional elements. Not a barcode — serves a structural role (e.g., prevents sequence bias, aids ligation). | `X` | 4bp spacer → `XXXX` |
| Capture sequence / target | Variable biological sequence (antibody tag, guide RNA spacer, hashtag). NOT cDNA — these are short, defined-length captured sequences. | `V` | 15bp antibody barcode → `VVVVVVVVVVVVVVV` |

### Rules for identifying variable regions:
- If the protocol says "barcode" without qualification and there's only one barcoding step, use `B`
- If there are multiple barcoding rounds (combinatorial indexing), distinguish them: first round = `R` (RT), second round = `L` (ligation), third round as appropriate
- If a sequence is random/degenerate and used for deduplication, it's a UMI (`U`)
- If a sequence identifies the sample for demultiplexing, it's a sample index (`I`)
- If you see "N" bases in the protocol representing a variable region, determine WHICH type it is from context — do not output `N`

## Output format

Respond with ONLY a JSON object (no markdown, no backticks, no explanation). The JSON must contain:

1. `protocol_name`: Name of the protocol/kit
2. `library_sequence`: The full library construct from 5' to 3' as a single string concatenating all segments. Use real bases for known sequences and placeholder characters for variable regions. Do NOT include cDNA/genomic insert — skip it entirely.
3. `segments`: Array of objects in 5'-to-3' order, each with:
   - `name`: Human-readable name (e.g., "P5 adapter", "Cell barcode", "UMI")
   - `type`: One of "known", "barcode", "umi", "index", "ligation", "rt_barcode", "tn5_barcode", "linker", "capture"
   - `sequence`: The actual bases for known sequences, or the placeholder string for variable regions
   - `length`: Length in base pairs
   - `char`: (only for variable regions) The placeholder character used
   - `role`: (only for variable regions) Brief explanation of what this region does
4. `placeholder_key`: Object mapping each placeholder character used to its meaning

## Rules

- Output the FULL construct from P5 to P7 (or equivalent flow cell adapters)
- Include ALL adapter and primer sequences with exact bases
- Do NOT include cDNA, genomic DNA, or any insert sequence — skip it, concatenate the flanking segments directly
- Every variable region must use the correct placeholder character from the table above — NEVER use `N`
- Double-check that each segment's `length` matches the actual character count in its `sequence`
- Double-check that concatenating all segment sequences equals `library_sequence`
- If there are multiple chemistry versions, output the one specified in the input (or the latest if not specified)

## Example

For a simple 10x-style library, the output would look like:

{
  "protocol_name": "Example 3' Gene Expression Kit",
  "library_sequence": "AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTCTTCCGATCTBBBBBBBBBBBBBBBBUUUUUUUUUUUUAGATCGGAAGAGCACACGTCTGAACTCCAGTCACIIIIIIIIATCTCGTATGCCGTCTTCTGCTTG",
  "segments": [
    { "name": "P5 adapter", "type": "known", "sequence": "AATGATACGGCGACCACCGAGATCTACAC", "length": 29 },
    { "name": "TruSeq Read 1 primer", "type": "known", "sequence": "TCTTTCCCTACACGACGCTCTTCCGATCT", "length": 28 },
    { "name": "Cell barcode", "type": "barcode", "char": "B", "sequence": "BBBBBBBBBBBBBBBB", "length": 16, "role": "Identifies cell of origin, from barcode whitelist" },
    { "name": "UMI", "type": "umi", "char": "U", "sequence": "UUUUUUUUUUUU", "length": 12, "role": "Random sequence for PCR deduplication" },
    { "name": "TruSeq Read 2 primer", "type": "known", "sequence": "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC", "length": 34 },
    { "name": "Sample index", "type": "index", "char": "I", "sequence": "IIIIIIII", "length": 8, "role": "Identifies sample for demultiplexing, i7 index" },
    { "name": "P7 adapter", "type": "known", "sequence": "ATCTCGTATGCCGTCTTCTGCTTG", "length": 24 }
  ],
  "placeholder_key": { "B": "cell barcode", "U": "UMI", "I": "sample index" }
}

Now parse the following protocol:
