# Example Output: 10x Chromium Single Cell 3' Gene Expression v4

This file shows the target output format for a parsed sequencing protocol. Use this as a structural guide — the content will differ for each protocol, but the organization and level of detail should match.

---

## Metadata

- **Protocol name**: 10x Chromium Single Cell 3' Gene Expression v4 (GEM-X)
- **Company/Manufacturer**: 10x Genomics
- **Chemistry version**: v4
- **Published/Released**: 2024
- **Document reference**: CG000731 Rev B
- **Source URL**: https://cdn.10xgenomics.com/image/upload/v1725314293/support-documents/CG000731_ChromiumGEM-X_SingleCell3v4_UserGuide_RevB.pdf
- **Processing software**: Cell Ranger
- **Associated files**: 3M-february-2018.txt.gz (barcode whitelist)

### Brief description

Droplet-based single-cell RNA-seq capturing polyadenylated mRNA via oligo-dT beads with cell barcodes and UMIs in GEM-X emulsion, sequenced on Illumina.

---

## 1. Adapter and Primer Sequences

### Bead oligo (oligo-dT)

    |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](T)30VN -3'

### Template Switching Oligo (TSO)

    5'- AAGCAGTGGTATCAACGCAGAGTACATrGrGrG -3'

### cDNA amplification primers

**Forward primer** (primes from the Read 1 / bead-oligo end):

    5'- CTACACGACGCTCTTCCGATCT -3'

**Reverse primer** (primes from the TSO end):

    5'- AAGCAGTGGTATCAACGCAGAG -3'

### Illumina TruSeq Read 1 primer

    5'- ACACTCTTTCCCTACACGACGCTCTTCCGATCT -3'

### Illumina TruSeq Read 2 primer

    5'- GTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT -3'

### TruSeq adapter (double-stranded, T overhang)

    5'-  GATCGGAAGAGCACACGTCTGAACTCCAGTCA -3'
    3'- TCTAGCCTTCTCG -5'

### Library PCR primers

**Primer 1** (P5-end):

    5'- AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTC -3'

**Primer 2** (P7-end, includes sample index):

    5'- CAAGCAGAAGACGGCATACGAGAT[8-bp sample index]GTGACTGGAGTTCAGACGTGT -3'

### Sample index sequencing primer

    5'- GATCGGAAGAGCACACGTCTGAACTCCAGTCAC -3'

### Illumina P5 adapter

    5'- AATGATACGGCGACCACCGAGATCTACAC -3'

### Illumina P7 adapter

    5'- CAAGCAGAAGACGGCATACGAGAT -3'

---

## 2. Step-by-Step Library Generation

### Step 1: mRNA capture and reverse transcription

Polyadenylated mRNA is captured by the oligo-dT bead inside the GEM droplet. MMLV reverse transcriptase synthesizes first-strand cDNA.

    |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](T)30VN-------->
                                                                (A)30BXXXXXXXXXXXXXXXXXXXXX -5'

### Step 2: Non-templated C-tailing

The terminal transferase activity of MMLV adds extra C nucleotides to the 3' end of the cDNA.

    |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](dT)VXXXXXXXXX...XXXXXXXXXCCC -3'
                                                                (pA)BXXXXXXXXX...XXXXXXXXX    -5'

### Step 3: Template switching and second-strand synthesis

The TSO anneals to the C-tail. Reverse transcriptase switches template and extends through the TSO sequence.

    |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](dT)VXXXXXXXXX...XXXXXXXXXCCC------->
                                                       <--------(pA)BXXXXXXXXX...XXXXXXXXXGGGTACATGAGACGCAACTATGGTGACGAA -5'

### Step 4: Full-length cDNA amplification

cDNA Forward and Reverse primers amplify the full-length cDNA.

       5'- CTACACGACGCTCTTCCGATCT-------->
    |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](dT)VXXXXXXXXX...XXXXXXXXXCCCATGTACTCTGCGTTGATACCACTGCTT -3'
       3'- GATGTGCTGCGAGAAGGCTAGA[16-bp cell barcode][12-bp UMI](pA)BXXXXXXXXX...XXXXXXXXXGGGTACATGAGACGCAACTATGGTGACGAA -5'
                                                                                    <--------TACATGAGACGCAACTATGGTGACGAA -5'

### Step 5: Fragmentation and A-tailing

Fragmentase cleaves the cDNA. A-tails are added to fragment ends. The target fragment retains the barcode end:

    5'-   CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](dT)VXXXXXXXXX...XXXXXXXXX*A -3'
    3'- A*GATGTGCTGCGAGAAGGCTAGA[16-bp cell barcode][12-bp UMI](pA)BXXXXXXXXX...XXXXXXXXX   -5'

### Step 6: Adapter ligation

Double-stranded TruSeq adapter (with T overhang) is ligated to the A-tailed target fragment.

    5'-   CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI](dT)VXXX...XXXAGATCGGAAGAGCACACGTCTGAACTCCAGTCA -3'
    3'- A*GATGTGCTGCGAGAAGGCTAGA[16-bp cell barcode][12-bp UMI](pA)BXXX...XXXTCTAGCCTTCTCG -5'

### Step 7: Library PCR

Library PCR Primers 1 and 2 amplify the final library, adding the full P5/P7 adapters and sample index.

    5'-  AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTC--------->
                                       5'-   CTACACGACGCTCTTCCGATCT[cell barcode][UMI](dT)VXXX...XXXAGATCGGAAGAGCACACGTCTGAACTCCAGTCA -3'
                                       3'- A*GATGTGCTGCGAGAAGGCTAGA[cell barcode][UMI](pA)BXXX...XXXTCTAGCCTTCTCG                     -5'
                                                                                                     <-----------TGTGCAGACTTGAGGTCAGTG[8-bp sample index]TAGAGCATACGGCAGAAGACGAAC -5'

### Step 8: Final library structure

    5'- AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTCTTCCGATCT[cell barcode][UMI](dT)VXXX...XXXAGATCGGAAGAGCACACGTCTGAACTCCAGTCA[sample index]ATCTCGTATGCCGTCTTCTGCTTG -3'
    3'- TTACTATGCCGCTGGTGGCTCTAGATGTGAGAAAGGGATGTGCTGCGAGAAGGCTAGA[cell barcode][UMI](pA)BXXX...XXXTCTAGCCTTCTCGTGTGCAGACTTGAGGTCAGT[sample index]TAGAGCATACGGCAGAAGACGAAC -5'
              Illumina P5                   TruSeq Read 1              16 bp     12 bp           cDNA          TruSeq Read 2            8 bp          Illumina P7

---

## 3. Library Sequencing

### Read 1: Cell barcode + UMI (28 cycles)

TruSeq Read 1 primer hybridizes and sequences the bottom strand. Reads the 16-bp cell barcode and 12-bp UMI.
```
                         5'- ACACTCTTTCCCTACACGACGCTCTTCCGATCT------------------------->
3'- TTACTATGCCGCTGGTGGCTCTAGATGTGAGAAAGGGATGTGCTGCGAGAAGGCTAGANNNNNNNNNNNNNNNNNNNNNNNNNN(pA)BXXX...XXXTCTAGCCTTCTCGTGTGCAGACTTGAGGTCAGTGNNNNNNNNTAGAGCATACGGCAGAAGACGAAC -5'
```
### Index read: Sample index (8 cycles)

Sample index sequencing primer reads the 8-bp sample index (bottom strand as template).

```
                                                                                                   5'- GATCGGAAGAGCACACGTCTGAACTCCAGTCAC------->
3'- TTACTATGCCGCTGGTGGCTCTAGATGTGAGAAAGGGATGTGCTGCGAGAAGGCTAGANNNNNNNNNNNNNNNNNNNNNNNNNN(pA)BXXX...XXXTCTAGCCTTCTCGTGTGCAGACTTGAGGTCAGTGNNNNNNNNTAGAGCATACGGCAGAAGACGAAC -5'
```

### Read 2: cDNA insert (98 cycles)

After cluster regeneration, TruSeq Read 2 primer sequences the top strand (cDNA).

```
5'- AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTCTTCCGATCTNNNNNNNNNNNNNNNNNNNNNNNNNN(dT)VXXX...XXXAGATCGGAAGAGCACACGTCTGAACTCCAGTCACNNNNNNNNATCTCGTATGCCGTCTTCTGCTTG -3'
                                                                                                <-----TCTAGCCTTCTCGTGTGCAGACTTGAGGTCAGTG -5'
```
