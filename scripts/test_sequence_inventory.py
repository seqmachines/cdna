#!/usr/bin/env python3
from __future__ import annotations

from sequence_inventory import extract_sequence_inventory, load_inventory_rows


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


TENX_CHROMIUM_5_SECTION_1 = """Beads-TSO:
              V1 (PN-220112) & V2 (PN-1000264): |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG -3'
                               V3 (PN-2001129): |--5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI]TTTCTTATATrGrGrG -3'
Poly-dT RT primer (PN-2000007): 5'- AAGCAGTGGTATCAACGCAGAGTAC TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN -3'
cDNA Primer Mix (for cDNA amplification, PN-220106):
            Forward primer: 5'- CTACACGACGCTCTTCCGATCT -3'
            Reverse primer: 5'- AAGCAGTGGTATCAACGCAG -3'
Illumina Truseq Read 1 primer: 5'- ACAC TCTTTCCCTACACGACGCTCTTCCGATCT -3'
Illumina Truseq Read 2 primer: 5'- GTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT -3'
Truseq adapter (double stranded DNA with a T overhang, PN-220026):
            5'-  GATCGGAAGAGCACACGTCTGAACTCCAGTCAC -3'
            3'- TCTAGCCTTCTCG -5'
Library PCR primer 1 (PN-220111): 5'- AATGATACGGCGACCACCGAGATCTACAC TCTTTCCCTACACGACGCTC -3'
Library PCR primer 2 (PN-220103): 5'- CAAGCAGAAGACGGCATACGAGAT[8-bp sample index]GTGACTGGAGTTCAGACGTGT -3'
Sample index sequencing primer: 5'- GATCGGAAGAGCACACGTCTGAACTCCAGTCAC -3'
Illumina P5 adapter: 5'- AATGATACGGCGACCACCGAGATCTACAC -3'
Illumina P7 adapter: 5'- CAAGCAGAAGACGGCATACGAGAT -3'
"""


def main() -> int:
    rows = load_inventory_rows()
    assert_true(rows, "expected TSV inventory rows to load")

    known = extract_sequence_inventory(
        "Illumina TruSeq Read 1 primer ACACTCTTTCCCTACACGACGCTCTTCCGATCT\n"
        "Bridge-Oligo_truseq_ddC CGTCGTGTAGGGAAAGAGTGT GACGCTGCCGACGA[ddC]\n"
    )
    known_sources = {candidate["source"] for candidate in known["candidates"]}
    assert_true("known_inventory" in known_sources, "expected known inventory hits")
    assert_true(
        any(candidate["inventory_id"] == "scifi_bridge_ddc" for candidate in known["candidates"]),
        "expected whitespace-tolerant Bridge-Oligo match",
    )

    novel = extract_sequence_inventory("Primer A: TTGACCTGACCTGACCTGACCTA\n")
    assert_true(
        any(candidate["source"] == "regex" for candidate in novel["candidates"]),
        "expected regex fallback for novel primer",
    )

    heading = extract_sequence_inventory("LIBRARY CONSTRUCTION AND AMPLIFICATION\n")
    assert_true(not heading["candidates"], "expected uppercase heading to be ignored")

    tenx = extract_sequence_inventory(TENX_CHROMIUM_5_SECTION_1)
    tenx_sequences = {candidate["sequence"] for candidate in tenx["candidates"]}
    tenx_names = {candidate["name_hint"] for candidate in tenx["candidates"]}
    assert_true(
        "CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG"
        in tenx_sequences,
        "expected full Beads-TSO v1/v2 composite sequence",
    )
    assert_true(
        "CTACACGACGCTCTTCCGATCT[16-bp cell barcode][12-bp UMI]TTTCTTATATrGrGrG"
        in tenx_sequences,
        "expected full Beads-TSO v3 composite sequence",
    )
    assert_true(
        "CAAGCAGAAGACGGCATACGAGAT[8-bp sample index]GTGACTGGAGTTCAGACGTGT"
        in tenx_sequences,
        "expected Library PCR primer 2 placeholder sequence",
    )
    assert_true(
        "Truseq adapter (double stranded DNA with a T overhang, PN-220026) - 3_to_5 strand"
        in tenx_names,
        "expected named bottom strand for double-stranded TruSeq adapter",
    )
    assert_true(
        not any(
            candidate["source_text"].startswith("Library PCR primer 1")
            and candidate["sequence"] == "AATGATACGGCGACCACCGAGATCTACAC"
            for candidate in tenx["candidates"]
        ),
        "expected known P5 subsequence inside Library PCR primer 1 to be deduplicated",
    )

    print("sequence inventory smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
