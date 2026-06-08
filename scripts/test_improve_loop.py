from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdna_engine.cli import app
from cdna_engine.oligos.improve import build_runtime_memory, compare_to_ground_truth, component_memory_prompt_tsv, filter_memory_for_protocol, load_ground_truth, load_runtime_memory, memory_by_name, memory_prompt_tsv, prediction_output_payload, resolve_oligos, sanitize_links, sanitize_protocol_brief, semantic_sequence_and_components
from cdna_engine.oligos.normalizer import display_name_key, normalize_sequence
from cdna_engine.oligos.scanner import parse_text_blocks, scan_sequence_candidates, source_files
from cdna_engine.oligos.schema import Oligo, ProtocolOligoSet
from scripts.build_assembled_component_memory import build_rows


runner = CliRunner()


def test_source_files_skip_office_lock_files(tmp_path: Path) -> None:
    real_workbook = tmp_path / "s3-ATAC.xlsx"
    lock_workbook = tmp_path / "~$s3-ATAC.xlsx"
    real_workbook.write_bytes(b"not parsed in this test")
    lock_workbook.write_bytes(b"")

    assert source_files(tmp_path) == [real_workbook.resolve()]
    assert source_files(lock_workbook) == []


def test_component_memory_builder_deduplicates_relationships(tmp_path: Path) -> None:
    protocol_root = tmp_path / "protocols"
    for protocol_id in ["protocol_a", "protocol_b"]:
        protocol_dir = protocol_root / protocol_id
        protocol_dir.mkdir(parents=True)
        (protocol_dir / "groundtruth_oligos.json").write_text(
            json.dumps(
                {
                    "protocol_id": protocol_id,
                    "protocol_name": protocol_id.replace("_", " ").title(),
                    "oligos": [
                        {
                            "name": "Illumina Nextera Read 2 primer",
                            "role": "primer",
                            "kind": "assembled",
                            "direction": "5_to_3",
                            "sequence": "GTCTCGTGGGCTCGGAGATGTGTATAAGAGACAG",
                            "components": [
                                {
                                    "order": 1,
                                    "name": "Nextera N7xx primer entry point (s7)",
                                    "role": "adapter",
                                    "sequence": "GTCTCGTGGGCTCGG",
                                },
                                {
                                    "order": 2,
                                    "name": "Nextera Tn5 binding site (19-bp Mosaic End (ME))",
                                    "role": "tn5_binding_site",
                                    "sequence": "AGATGTGTATAAGAGACAG",
                                },
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    rows = build_rows(protocol_root)

    assert len(rows) == 2
    assert {row["component_name"] for row in rows} == {
        "Nextera N7xx primer entry point (s7)",
        "Nextera Tn5 binding site (19-bp Mosaic End (ME))",
    }
    assert {row["protocol_count"] for row in rows} == {2}
    assert {row["source_protocol_ids"] for row in rows} == {"protocol_a;protocol_b"}


def test_component_memory_builder_drops_assembled_child_components(tmp_path: Path) -> None:
    protocol_root = tmp_path / "protocols"
    protocol_dir = protocol_root / "protocol_a"
    protocol_dir.mkdir(parents=True)
    read2_sequence = "GTCTCGTGGGCTCGGAGATGTGTATAAGAGACAG"
    (protocol_dir / "groundtruth_oligos.json").write_text(
        json.dumps(
            {
                "protocol_id": "protocol_a",
                "protocol_name": "Protocol A",
                "oligos": [
                    {
                        "name": "Library P7 Index",
                        "role": "primer",
                        "kind": "assembled",
                        "direction": "5_to_3",
                        "sequence": f"CAAGCAGAAGACGGCATACGAGAT[i7]{read2_sequence}",
                        "components": [
                            {
                                "order": 1,
                                "name": "Illumina P7 adapter",
                                "role": "adapter",
                                "sequence": "CAAGCAGAAGACGGCATACGAGAT",
                            },
                            {
                                "order": 2,
                                "name": "Illumina Nextera Read 2 primer",
                                "role": "primer",
                                "sequence": read2_sequence,
                            },
                        ],
                    },
                    {
                        "name": "Illumina Nextera Read 2 primer",
                        "role": "primer",
                        "kind": "assembled",
                        "direction": "5_to_3",
                        "sequence": read2_sequence,
                        "components": [
                            {
                                "order": 1,
                                "name": "Nextera N7xx primer entry point (s7)",
                                "role": "primer_site",
                                "sequence": "GTCTCGTGGGCTCGG",
                            },
                            {
                                "order": 2,
                                "name": "Nextera Tn5 binding site (19-bp Mosaic End (ME))",
                                "role": "tn5_binding_site",
                                "sequence": "AGATGTGTATAAGAGACAG",
                            },
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = build_rows(protocol_root)
    relationships = {(row["parent_oligo_name"], row["component_name"]) for row in rows}

    assert ("Library P7 Index", "Illumina P7 adapter") in relationships
    assert ("Library P7 Index", "Illumina Nextera Read 2 primer") not in relationships
    assert ("Illumina Nextera Read 2 primer", "Nextera N7xx primer entry point (s7)") in relationships
    assert ("Illumina Nextera Read 2 primer", "Nextera Tn5 binding site (19-bp Mosaic End (ME))") in relationships


def write_fixture_protocol(root: Path, split: str = "train") -> tuple[Path, Path, Path]:
    protocol_id = "fixture_protocol"
    protocol_name = "Fixture Protocol"
    protocol_dir = root / "protocols" / protocol_id
    protocol_dir.mkdir(parents=True)
    (protocol_dir / "protocol.txt").write_text(
        "\n".join(
            [
                "Template Switching Oligo (TSO): 5'- AAGCAGTGGTATCAACGCAGAGTGAATrGrGrG -3'",
                "Illumina P5 adapter: 5'- AATGATACGGCGACCACCGAGATCTACAC -3'",
            ]
        ),
        encoding="utf-8",
    )
    ground_truth = {
        "protocol_id": protocol_id,
        "protocol_name": protocol_name,
        "oligos": [
            {
                "oligo_id": "oligo_template_switching_oligo_tso",
                "protocol_id": protocol_id,
                "protocol_name": protocol_name,
                "name": "Template Switching Oligo (TSO)",
                "aliases": [],
                "role": "oligo",
                "kind": "single",
                "sequence": "AAGCAGTGGTATCAACGCAGAGTGAATrGrGrG",
                "direction": "5_to_3",
                "components": [],
                "sequence_source": "curated_ground_truth",
                "memory_id": None,
                "evidence": [],
                "notes": None,
            },
            {
                "oligo_id": "oligo_illumina_p5_adapter",
                "protocol_id": protocol_id,
                "protocol_name": protocol_name,
                "name": "Illumina P5 adapter",
                "aliases": [],
                "role": "adapter",
                "kind": "single",
                "sequence": "AATGATACGGCGACCACCGAGATCTACAC",
                "direction": "5_to_3",
                "components": [],
                "sequence_source": "curated_ground_truth",
                "memory_id": None,
                "evidence": [],
                "notes": None,
            },
        ],
    }
    gt_path = protocol_dir / "groundtruth_oligos.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8")
    return protocol_dir, gt_path, root


def test_schema_roundtrip_and_memory_guard() -> None:
    oligo = Oligo(
        oligo_id="oligo_a",
        protocol_id="p",
        protocol_name="P",
        name="Primer A",
        role="primer",
        kind="single",
        sequence="ACGTACGTACGT",
        sequence_source="explicit_in_protocol",
    )
    payload = ProtocolOligoSet(protocol_id="p", protocol_name="P", split="train", oligos=[oligo]).model_dump(mode="json")
    assert ProtocolOligoSet.model_validate(payload).oligos[0].sequence == "ACGTACGTACGT"
    compact = prediction_output_payload(ProtocolOligoSet(protocol_id="p", protocol_name="P", split="train", oligos=[oligo]))
    assert "protocol_id" not in compact["oligos"][0]
    assert "protocol_name" not in compact["oligos"][0]
    reloaded = ProtocolOligoSet.model_validate(compact)
    assert reloaded.oligos[0].protocol_id == "p"
    assert reloaded.oligos[0].protocol_name == "P"
    with pytest.raises(ValueError):
        Oligo(
            oligo_id="oligo_b",
            protocol_id="p",
            protocol_name="P",
            name="Primer B",
            role="primer",
            kind="single",
            sequence="ACGTACGTACGT",
            sequence_source="memory_completed",
        )


def test_ground_truth_loader_injects_split(tmp_path: Path) -> None:
    _protocol_dir, gt_path, root = write_fixture_protocol(tmp_path, split="train")
    (root / "protocol_split.tsv").write_text("Split\tprotocol_name\ntest\tFixture Protocol\n", encoding="utf-8")
    loaded = load_ground_truth(gt_path, "fixture_protocol", "eval")
    assert loaded.split == "eval"
    assert loaded.oligos[0].sequence_source == "curated_ground_truth"


def test_normalizer_preserves_modifications_and_placeholders() -> None:
    assert normalize_sequence("5'- /phos/ACGT[16-bp cell barcode][10-bp UMI]rGrGrG/ddU/ -3'") == (
        "/5Phos/ACGT[16-bp cell barcode][10-bp UMI]rGrGrG/ddU/"
    )
    assert normalize_sequence("5'- A C G T +A * (dU) -3'") == "ACGT+A*(dU)"
    assert normalize_sequence("5'- AAGCAGTGGTATCAACGCAGAGTACT(30)VN -3'") == (
        "AAGCAGTGGTATCAACGCAGAGTAC" + ("T" * 30) + "VN"
    )


def test_candidate_scanner_captures_rna_suffix_and_placeholders() -> None:
    blocks = parse_text_blocks(
        "Beads-TSO: 5'- CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG -3'",
        "fixture.txt",
    )
    candidates = scan_sequence_candidates(blocks)
    sequences = {candidate["normalized_sequence"] for candidate in candidates}
    assert "CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG" in sequences


def test_candidate_scanner_joins_wrapped_oriented_sequence() -> None:
    blocks = parse_text_blocks(
        "The carefully designed SMARTer II A oligo (5′-AAGCAGTGGTATCAACGCA\n"
        "GAGTACATrGrGrG-3′, 1 M betaine)",
        "SMART-seq.pdf",
    )
    candidates = scan_sequence_candidates(blocks)
    sequences = {candidate["normalized_sequence"] for candidate in candidates}
    assert "AAGCAGTGGTATCAACGCAGAGTACATrGrGrG" in sequences
    assert "AAGCAGTGGTATCAACGCA" not in sequences


def test_protocol_brief_sanitizer_removes_internal_pipeline_language() -> None:
    brief = sanitize_protocol_brief(
        {
            "summary": "The chunks report a new chromatin accessibility method. It profiles open chromatin in single cells.",
            "major_steps": ["Read source chunks", "Isolate nuclei", "Write prediction.json"],
        }
    )
    assert brief["summary"] == "It profiles open chromatin in single cells."
    assert brief["major_steps"] == ["Isolate nuclei"]


def test_semantic_sequence_components_for_gel_bead_placeholders() -> None:
    sequence, components = semantic_sequence_and_components(
        "CTACACGACGCTCTTCCGATCT-NNNNNNNNNNNNNNNN-NNNNNNNNNN-TTTCTTATATrGrGrG",
        "Gel Bead Primer",
    )
    assert sequence == "CTACACGACGCTCTTCCGATCTBBBBBBBBBBBBBBBBUUUUUUUUUUTTTCTTATATrGrGrG"
    assert [(component.role, component.sequence) for component in components] == [
        ("cell_barcode", "BBBBBBBBBBBBBBBB"),
        ("umi", "UUUUUUUUUU"),
    ]
    bracket_sequence, bracket_components = semantic_sequence_and_components(
        "CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG",
        "Beads-TSO",
    )
    assert bracket_sequence == "CTACACGACGCTCTTCCGATCTBBBBBBBBBBBBBBBBUUUUUUUUUUTTTCTTATATrGrGrG"
    assert [(component.role, component.sequence) for component in bracket_components] == [
        ("cell_barcode", "BBBBBBBBBBBBBBBB"),
        ("umi", "UUUUUUUUUU"),
    ]


def test_semantic_sequence_components_for_sci_rna_rt_index() -> None:
    sequence, components = semantic_sequence_and_components(
        "ACGACGCTCTTCCGATCTNNNNNNNN[10bp index]TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN",
        "Barcoded RT primer",
    )
    assert sequence == "ACGACGCTCTTCCGATCTUUUUUUUUBBBBBBBBBBTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN"
    assert [(component.role, component.sequence) for component in components[:2]] == [
        ("umi", "UUUUUUUU"),
        ("rt_barcode", "BBBBBBBBBB"),
    ]

    ground_truth_sequence, _components = semantic_sequence_and_components(
        "ACGACGCTCTTCCGATCT[8-bp UMI][10-bp RT barcode]TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN",
        "Barcoded RT primer",
    )
    assert ground_truth_sequence == sequence


def test_compare_normalizes_placeholders_and_optional_part_numbers() -> None:
    prediction = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="oligo_beads_tso",
                protocol_id="p",
                protocol_name="P",
                name="Beads-TSO",
                role="oligo",
                kind="assembled",
                sequence="CTACACGACGCTCTTCCGATCTBBBBBBBBBBBBBBBBUUUUUUUUUUTTTCTTATATrGrGrG",
                sequence_source="explicit_in_protocol",
            ),
            Oligo(
                oligo_id="oligo_library_pcr_1",
                protocol_id="p",
                protocol_name="P",
                name="Library PCR primer 1",
                role="primer",
                kind="assembled",
                sequence="AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTC",
                sequence_source="memory_completed",
                memory_id="mem_library_pcr_1",
            ),
            Oligo(
                oligo_id="oligo_vdj_forward",
                protocol_id="p",
                protocol_name="P",
                name="VDJ Forward Primer",
                role="primer",
                kind="single",
                sequence="GATCTACACTCTTTCCCTACACGACGC",
                sequence_source="explicit_in_protocol",
                notes="assay=vdj",
            ),
        ],
    )
    ground_truth = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="gt_beads_tso",
                protocol_id="p",
                protocol_name="P",
                name="Beads-TSO",
                role="oligo",
                kind="assembled",
                sequence="CTACACGACGCTCTTCCGATCT[16-bp cell barcode][10-bp UMI]TTTCTTATATrGrGrG",
                sequence_source="curated_ground_truth",
            ),
            Oligo(
                oligo_id="gt_library_pcr_1",
                protocol_id="p",
                protocol_name="P",
                name="Library PCR primer 1 (PN-220111)",
                role="primer",
                kind="assembled",
                sequence="AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTC",
                sequence_source="curated_ground_truth",
            ),
        ],
    )
    comparison = compare_to_ground_truth(prediction, ground_truth)
    assert comparison["metrics"]["oligo_name_recall"]["numerator"] == 2
    assert comparison["metrics"]["oligo_name_precision"]["denominator"] == 2
    assert comparison["metrics"]["sequence_exact_match"]["numerator"] == 2
    assert comparison["metrics"]["matched_sequence_similarity_mean"]["value"] == 1.0
    assert comparison["metrics"]["allowed_extra_vdj"]["numerator"] == 1
    assert comparison["allowed_extra_names"] == [display_name_key("VDJ Forward Primer")]
    assert comparison["failures"] == []


def test_compare_tolerates_high_similarity_matched_sequences() -> None:
    prediction = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="oligo_primer_a",
                protocol_id="p",
                protocol_name="P",
                name="Primer A",
                role="primer",
                kind="single",
                sequence="ACGTACGTACGTACGTACGA",
                sequence_source="explicit_in_protocol",
            )
        ],
    )
    ground_truth = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="gt_primer_a",
                protocol_id="p",
                protocol_name="P",
                name="Primer A",
                role="primer",
                kind="single",
                sequence="ACGTACGTACGTACGTACGT",
                sequence_source="curated_ground_truth",
            )
        ],
    )
    comparison = compare_to_ground_truth(prediction, ground_truth, sequence_similarity_threshold=0.95)
    assert comparison["metrics"]["sequence_exact_match"]["numerator"] == 0
    assert comparison["metrics"]["sequence_similarity_pass"]["numerator"] == 1
    assert comparison["metrics"]["matched_sequence_similarity_mean"]["value"] == 0.95
    assert comparison["tolerated_sequence_variants"][0]["sequence_similarity"] == 0.95
    assert comparison["failures"] == []


def test_compare_reports_best_sequence_similarity_without_name_match() -> None:
    prediction = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="oligo_protocol_name",
                protocol_id="p",
                protocol_name="P",
                name="Protocol-specific local name",
                role="primer",
                kind="single",
                sequence="AAGCAGTGGTATCAACGCAGAGT",
                sequence_source="explicit_in_protocol",
            )
        ],
    )
    ground_truth = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="gt_ispcr",
                protocol_id="p",
                protocol_name="P",
                name="ISPCR / TSO PCR primer",
                role="primer",
                kind="single",
                sequence="AAGCAGTGGTATCAACGCAGAGT",
                sequence_source="curated_ground_truth",
            )
        ],
    )
    comparison = compare_to_ground_truth(prediction, ground_truth)
    assert comparison["metrics"]["oligo_name_recall"]["numerator"] == 0
    assert comparison["metrics"]["sequence_best_match_mean"]["value"] == 1.0
    assert comparison["sequence_best_matches"][0]["best_gt_name"] == "ISPCR / TSO PCR primer"


def test_compare_ignores_auxiliary_component_predictions_not_in_ground_truth() -> None:
    prediction = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="oligo_tso",
                protocol_id="p",
                protocol_name="P",
                name="Template Switching Oligo (TSO)",
                role="oligo",
                kind="assembled",
                sequence="AAGCAGTGGTATCAACGCAGAGTACATrGrGrG",
                components=[
                    {
                        "order": 1,
                        "name": "cDNA reverse primer",
                        "sequence": "AAGCAGTGGTATCAACGCAGAGTACAT",
                        "role": "primer",
                    }
                ],
                sequence_source="explicit_in_protocol",
            ),
            Oligo(
                oligo_id="oligo_cdna_reverse",
                protocol_id="p",
                protocol_name="P",
                name="cDNA reverse primer",
                role="primer",
                kind="single",
                sequence="AAGCAGTGGTATCAACGCAGAGTACAT",
                sequence_source="memory_completed",
                memory_id="cmem_000001",
            ),
        ],
    )
    ground_truth = ProtocolOligoSet(
        protocol_id="p",
        protocol_name="P",
        split="train",
        oligos=[
            Oligo(
                oligo_id="gt_tso",
                protocol_id="p",
                protocol_name="P",
                name="Template Switching Oligo (TSO)",
                role="oligo",
                kind="assembled",
                sequence="AAGCAGTGGTATCAACGCAGAGTACATrGrGrG",
                sequence_source="curated_ground_truth",
            )
        ],
    )
    comparison = compare_to_ground_truth(prediction, ground_truth)
    assert comparison["metrics"]["oligo_name_precision"]["denominator"] == 1
    assert comparison["metrics"]["sequence_best_match_mean"]["denominator"] == 1
    assert comparison["ignored_auxiliary_component_names"] == ["cDNA reverse primer"]
    assert comparison["extra_names"] == []


def test_candidate_scanner_preserves_short_reverse_adapter_strand() -> None:
    blocks = parse_text_blocks(
        "Adaptor Oligos\n5'-GATCGGAAGAGCACACGTCTGAACTCCAGTCAC-3'\n3'-TCTAGCCTTCTCG-5'",
        "fixture.txt",
    )
    candidates = scan_sequence_candidates(blocks)
    by_sequence = {candidate["normalized_sequence"]: candidate for candidate in candidates}
    assert by_sequence["TCTAGCCTTCTCG"]["direction"] == "3_to_5"


def test_resolver_renames_high_similarity_common_memory_sequence() -> None:
    memory = {
        "oligo_nodes": [
            {
                "memory_id": "mem_tso_1",
                "oligo_id": "mem_tso_1",
                "name": "Template Switching Oligo (TSO)",
                "sequence": "AAGCAGTGGTATCAACGCAGAGTACATrGrGrG",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "source_protocol_ids": ["smart_like_a"],
                "protocol_count": 1,
                "aliases": [],
            },
            {
                "memory_id": "mem_tso_2",
                "oligo_id": "mem_tso_2",
                "name": "Template Switching Oligo (TSO)",
                "sequence": "AAGCAGTGGTATCAACGCAGAGTACATrGrG+G",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "source_protocol_ids": ["smart_like_b"],
                "protocol_count": 1,
                "aliases": [],
            },
        ],
        "assembled_component_edges": [],
    }
    prediction = resolve_oligos(
        "smart_seq",
        "SMART-seq",
        "train",
        [],
        [
            {
                "name": "SMARTer II A oligo",
                "candidate_id": "seq_0001",
                "component_candidate_ids": [],
                "role": "oligo",
                "kind": "single",
            }
        ],
        [
            {
                "candidate_id": "seq_0001",
                "block_id": "block_0001",
                "normalized_sequence": "AAGCAGTGGTATCAACGCAGAGTACATrGrGrG",
                "direction": "5_to_3",
                "nearby_text": "SMARTer II A oligo 5'-AAGCAGTGGTATCAACGCAGAGTACATrGrGrG-3'",
                "name_hint": "SMARTer II A oligo",
                "evidence": {"source_id": "fixture.txt", "page": 1, "section": None, "quote": "SMARTer II A oligo"},
            }
        ],
        memory,
    )
    assert prediction.oligos[0].name == "Template Switching Oligo (TSO)"
    assert prediction.oligos[0].memory_id == "mem_tso_1"


def test_resolver_promotes_canonical_memory_without_alias_component_leaks() -> None:
    def candidate(candidate_id: str, sequence: str, direction: str = "5_to_3", nearby_text: str | None = None) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "block_id": f"block_{candidate_id}",
            "normalized_sequence": sequence,
            "direction": direction,
            "nearby_text": nearby_text or sequence,
            "name_hint": f"Sequence candidate {candidate_id}",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": None, "quote": nearby_text or sequence},
        }

    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": "mem_cdna_mix",
                "name": "cDNA Primer Mix reverse (PN-220106)",
                "sequence": "AAGCAGTGGTATCAACGCAG",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
            {
                "memory_id": "mem_read2",
                "name": "Illumina TruSeq Read 2 primer",
                "sequence": "GTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
            {
                "memory_id": "mem_lpcr1",
                "name": "Library PCR primer 1 (PN-220111)",
                "sequence": "AATGATACGGCGACCACCGAGATCTACACTCTTTCCCTACACGACGCTC",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
            {
                "memory_id": "mem_sipcr",
                "name": "SI-PCR Primer B (PN-2000128)",
                "sequence": "AATGATACGGCGACCACCGAGA",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": ["Illumina P5 adapter"],
            },
            {
                "memory_id": "mem_adapter_reverse",
                "name": "TruSeq adapter reverse",
                "sequence": "TCTAGCCTTCTCG",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": ["TruSeq sample index sequencing primer forward"],
            },
        ],
    }
    sequence_candidates = [
        candidate("seq_cdna_reverse", "AAGCAGTGGTATCAACGCAGAG"),
        candidate(
            "seq_index_forward",
            "AATGATACGGCGACCACCGAGATCTACAC-N10-ACACTCTTTCCCTACACGACGCTC",
            nearby_text="Library PCR primer 1 contains AATGATACGGCGACCACCGAGATCTACAC-N10-ACACTCTTTCCCTACACGACGCTC",
        ),
        candidate(
            "seq_index_reverse",
            "CAAGCAGAAGACGGCATACGAGAT-N10-GTGACTGGAGTTCAGACGTGT",
            nearby_text="Illumina TruSeq Read 2 primer is present in CAAGCAGAAGACGGCATACGAGAT-N10-GTGACTGGAGTTCAGACGTGT",
        ),
        candidate("seq_adapter_forward", "GATCGGAAGAGCACACGTCTGAACTCCAGTCAC"),
        candidate("seq_adapter_reverse", "TCTAGCCTTCTCG", "3_to_5"),
        candidate("seq_generic_forward_1", "GATCTACACTCTTTCCCTACACGACGC"),
        candidate("seq_generic_forward_2", "GATCTACACTCTTTCCCTACACGACGC"),
        candidate("seq_generic_forward_3", "GATCTACACTCTTTCCCTACACGACGC"),
    ]
    links = [
        {"name": "cDNA reverse primer", "candidate_id": "seq_cdna_reverse", "role": "primer", "kind": "single"},
        {"name": "Forward Primer", "candidate_id": "seq_generic_forward_1", "role": "primer", "kind": "single"},
        {
            "name": "TruSeq adapter",
            "candidate_id": None,
            "component_candidate_ids": [
                {"candidate_id": "seq_adapter_forward", "role": "forward_strand"},
                {"candidate_id": "seq_adapter_reverse", "role": "reverse_strand"},
            ],
            "role": "adapter",
            "kind": "double_stranded",
        },
        {
            "name": "Final library construct",
            "candidate_id": None,
            "component_candidate_ids": [
                {"candidate_id": "seq_index_forward", "role": "forward_strand"},
                {"candidate_id": "seq_index_reverse", "role": "reverse_strand"},
            ],
            "role": "oligo",
            "kind": "double_stranded",
        },
    ]

    prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.txt"],
        links,
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    assert display_name_key("cDNA reverse primer") not in by_name
    assert display_name_key("VDJ Forward Primer") in by_name
    assert "assay=vdj" in (by_name[display_name_key("VDJ Forward Primer")].notes or "")
    assert display_name_key("Final library construct") not in by_name
    assert display_name_key("SI-PCR Primer B (PN-2000128)") not in by_name
    assert display_name_key("TruSeq adapter reverse") not in by_name
    assert by_name[display_name_key("cDNA Primer Mix reverse (PN-220106)")].sequence_source == "memory_completed"
    assert "assay=gex" in (by_name[display_name_key("cDNA Primer Mix reverse (PN-220106)")].notes or "")
    assert by_name[display_name_key("Illumina TruSeq Read 2 primer")].sequence == "GTGACTGGAGTTCAGACGTGTGCTCTTCCGATCT"
    assert by_name[display_name_key("Library PCR primer 1 (PN-220111)")].kind == "assembled"


def test_resolver_blocks_anchor_only_memory_overcompletion() -> None:
    def candidate(candidate_id: str, sequence: str, name_hint: str) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "block_id": f"block_{candidate_id}",
            "normalized_sequence": sequence,
            "direction": "5_to_3",
            "nearby_text": f"For PCR, use {name_hint}: {sequence}",
            "name_hint": name_hint,
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "PCR", "quote": f"{name_hint}: {sequence}"},
        }

    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": "mem_p5_adapter",
                "name": "Illumina P5 adapter",
                "sequence": "AATGATACGGCGACCACCGAGATCTACAC",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
            {
                "memory_id": "mem_cel_variant",
                "name": "Barcoded RT primer variant 1",
                "sequence": "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATC[8-bp cell barcode]TTTTTTTTTTTTTTTTTTTTTTTTV",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
            {
                "memory_id": "mem_cel_variant2",
                "name": "Barcoded RT primer variant 2",
                "sequence": "GCCGGTAATACGACTCACTATAGGGAGTTCTACAGTCCGACGATC[6-bp UMI][6-bp cell barcode]TTTTTTTTTTTTTTTTTTTTTTTTV",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
            },
        ],
    }
    sequence_candidates = [
        candidate(
            "seq_p5",
            "AATGATACGGCGACCACCGAGATCTACAC[i5]ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
            "P5 primer",
        ),
        candidate(
            "seq_rt",
            "ACGACGCTCTTCCGATCTNNNNNNNN[10bp index]TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN",
            "anchored oligo-dT primer",
        ),
    ]
    links = [
        {"name": "P5 primer", "candidate_id": "seq_p5", "role": "primer", "kind": "single"},
        {"name": "PCR P5 primer", "candidate_id": "seq_p5", "role": "primer", "kind": "single"},
        {"name": "anchored oligo-dT primer", "candidate_id": "seq_rt", "role": "primer", "kind": "assembled"},
        {"name": "Barcoded RT primer", "candidate_id": "seq_rt", "role": "primer", "kind": "assembled"},
    ]

    prediction = resolve_oligos(
        "sci_rna_seq",
        "sci-RNA-seq",
        "train",
        ["fixture.txt"],
        links,
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    assert display_name_key("Illumina P5 adapter") not in by_name
    assert display_name_key("Barcoded RT primer variant 1") not in by_name
    assert display_name_key("Barcoded RT primer variant 2") not in by_name
    assert display_name_key("P5 primer") not in by_name
    assert display_name_key("anchored oligo-dT primer") not in by_name
    assert by_name[display_name_key("PCR P5 primer")].sequence == (
        "AATGATACGGCGACCACCGAGATCTACAC[i5]ACACTCTTTCCCTACACGACGCTCTTCCGATCT"
    )
    assert by_name[display_name_key("Barcoded RT primer")].sequence == (
        "ACGACGCTCTTCCGATCTUUUUUUUUBBBBBBBBBBTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTVN"
    )


def test_resolver_adds_assembled_component_memory_records() -> None:
    sequence_candidates = [
        {
            "candidate_id": "seq_p5",
            "block_id": "block_seq_p5",
            "normalized_sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
            "direction": "5_to_3",
            "nearby_text": "P5 primer with i5 index",
            "name_hint": "P5 primer",
            "evidence": {
                "source_id": "fixture.txt",
                "page": 1,
                "section": "PCR",
                "quote": "P5 primer: AATGATACGGCGACCACCGAGATCTACAC[i5]ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
            },
        }
    ]
    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [],
        "assembled_component_edges": [
            {
                "component_memory_id": "cmem_read1",
                "parent_oligo_name": "PCR P5 primer",
                "parent_sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "Illumina TruSeq Read 1 primer",
                "component_role": "primer",
                "component_sequence": "ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
                "source_protocol_ids": ["other_protocol"],
                "source_protocol_names": ["Other Protocol"],
            }
        ],
    }
    prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.txt"],
        [{"name": "PCR P5 primer", "candidate_id": "seq_p5", "role": "primer", "kind": "assembled"}],
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    parent = by_name[display_name_key("PCR P5 primer")]
    assert parent.kind == "assembled"
    assert any(
        component.name == "Illumina TruSeq Read 1 primer"
        and component.sequence == "ACACTCTTTCCCTACACGACGCTCTTCCGATCT"
        for component in parent.components
    )
    assert by_name[display_name_key("Illumina TruSeq Read 1 primer")].sequence == "ACACTCTTTCCCTACACGACGCTCTTCCGATCT"
    assert by_name[display_name_key("Illumina TruSeq Read 1 primer")].memory_id == "cmem_read1"


def test_resolver_assembles_indexed_primer_from_component_links_without_alias_pollution() -> None:
    sequence_candidates = [
        {
            "candidate_id": "seq_p5",
            "block_id": "block_seq_p5",
            "normalized_sequence": "AATGATACGGCGACCACCGAGATCTACAC",
            "direction": "5_to_3",
            "nearby_text": "i5_Flowcell_Primer AATGATACGGCGACCACCGAGATCTACAC",
            "name_hint": "i5_Flowcell_Primer",
            "evidence": {"source_id": "fixture.tsv", "page": None, "section": "PCR", "quote": "i5_Flowcell_Primer"},
        },
        {
            "candidate_id": "seq_a14",
            "block_id": "block_seq_a14",
            "normalized_sequence": "TCGTCGGCAGCGTC",
            "direction": "5_to_3",
            "nearby_text": "i5_Nextera_A14_partial TCGTCGGCAGCGTC",
            "name_hint": "i5_Nextera_A14_partial",
            "evidence": {"source_id": "fixture.tsv", "page": None, "section": "PCR", "quote": "i5_Nextera_A14_partial"},
        },
    ]
    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": "mem_polluted",
                "name": "Unrelated adapter",
                "aliases": ["Beads-oligo", "Illumina Nextera Read 1 primer"],
                "sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]TCGTCGGCAGCGTC",
                "direction": "5_to_3",
            }
        ],
        "assembled_component_edges": [
            {
                "component_memory_id": "cmem_short_p5",
                "parent_oligo_name": "PCR_i5_primer",
                "parent_sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]TCGTCGGCAGCGTC",
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "P1_2nd_PCR",
                "component_role": "oligo",
                "component_sequence": "AATGATACGGCGACCACCGAGATC",
                "protocol_count": 1,
            },
            {
                "component_memory_id": "cmem_p5",
                "parent_oligo_name": "PCR_i5_primer",
                "parent_sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]TCGTCGGCAGCGTC",
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "Illumina P5 adapter",
                "component_role": "adapter",
                "component_sequence": "AATGATACGGCGACCACCGAGATCTACAC",
                "protocol_count": 2,
            },
            {
                "component_memory_id": "cmem_s5",
                "parent_oligo_name": "PCR_i5_primer",
                "parent_sequence": "AATGATACGGCGACCACCGAGATCTACAC[i5]TCGTCGGCAGCGTC",
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "Nextera N/S5xx primer entry point (s5)",
                "component_role": "primer_site",
                "component_sequence": "TCGTCGGCAGCGTC",
                "protocol_count": 2,
            },
        ],
    }

    prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.tsv"],
        [
            {
                "name": "Indexed i5 PCR primer pool",
                "candidate_id": None,
                "component_candidate_ids": [
                    {"candidate_id": "seq_p5", "role": "Illumina_P5_adapter"},
                    {"candidate_id": "seq_a14", "role": "Nextera_R1_A14_partial"},
                ],
                "role": "primer",
                "kind": "assembled",
                "notes": "Rows share the P5 flank and Nextera A14 flank with an 8-bp i5 sample index between them.",
            }
        ],
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    primer = by_name[display_name_key("PCR_i5_primer")]
    assert primer.sequence == "AATGATACGGCGACCACCGAGATCTACAC[i5]TCGTCGGCAGCGTC"
    assert primer.kind == "assembled"
    assert primer.aliases == []
    assert [component.sequence for component in primer.components] == [
        "AATGATACGGCGACCACCGAGATCTACAC",
        "[i5]",
        "TCGTCGGCAGCGTC",
    ]
    assert [component.sequence for component in primer.components].count("AATGATACGGCGACCACCGAGATCTACAC") == 1
    assert [component.sequence for component in primer.components].count("TCGTCGGCAGCGTC") == 1
    assert "AATGATACGGCGACCACCGAGATC" not in [component.sequence for component in primer.components]
    assert display_name_key("P1_2nd_PCR") not in by_name


def test_resolver_assembles_top_strand_without_bottom_strand_sequence() -> None:
    sequence_candidates = [
        {
            "candidate_id": "seq_top_left",
            "block_id": "block_seq_top_left",
            "normalized_sequence": "CGTGTGCTCTTCCGATCT",
            "direction": "5_to_3",
            "nearby_text": "TruSeq_R2_SBS12_partial CGTGTGCTCTTCCGATCT",
            "name_hint": "TruSeq_R2_SBS12_partial",
            "evidence": {"source_id": "fixture.tsv", "page": None, "section": "Tn5", "quote": "top left"},
        },
        {
            "candidate_id": "seq_top_right",
            "block_id": "block_seq_top_right",
            "normalized_sequence": "/ideoxyU/AGATGTGTATAAGAGACAG",
            "direction": "5_to_3",
            "nearby_text": "U-ME /ideoxyU/AGATGTGTATAAGAGACAG",
            "name_hint": "U-ME",
            "evidence": {"source_id": "fixture.tsv", "page": None, "section": "Tn5", "quote": "top right"},
        },
        {
            "candidate_id": "seq_bottom",
            "block_id": "block_seq_bottom",
            "normalized_sequence": "/5Phos/CTGTCTCTTATACACATCT",
            "direction": "5_to_3",
            "nearby_text": "Nextera_Mosaic_End_REVCOMP /5Phos/CTGTCTCTTATACACATCT",
            "name_hint": "Nextera_Mosaic_End_REVCOMP",
            "evidence": {"source_id": "fixture.tsv", "page": None, "section": "Tn5", "quote": "bottom"},
        },
    ]
    prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.tsv"],
        [
            {
                "name": "SBS12_18_UME_sci transposase oligo pool",
                "candidate_id": None,
                "component_candidate_ids": [
                    {"candidate_id": "seq_top_left", "role": "top_strand_Truseq_R2_SBS12_partial"},
                    {"candidate_id": "seq_top_right", "role": "top_strand_U-ME"},
                    {"candidate_id": "seq_bottom", "role": "bottom_strand_Nextera_Mosaic_End_REVCOMP"},
                ],
                "role": "adapter",
                "kind": "assembled",
                "notes": "Contains an 8-bp Tn5 index between the top strand pieces.",
            }
        ],
        sequence_candidates,
        {"oligo_nodes": [], "assembled_component_edges": []},
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    oligo = by_name[display_name_key("SBS12_18_UME_sci indexed Tn5 adapter")]
    assert oligo.sequence == "CGTGTGCTCTTCCGATCT[8-bp Tn5 index]/ideoxyU/AGATGTGTATAAGAGACAG"
    assert "/5Phos/CTGTCTCTTATACACATCT" not in [component.sequence for component in oligo.components]


def test_component_memory_keeps_longest_components_and_does_not_reverse_expand() -> None:
    n7_sequence = "CAAGCAGAAGACGGCATACGAGAT[i7]GTCTCGTGGGCTCGG"
    library_p7_sequence = "CAAGCAGAAGACGGCATACGAGAT[i7]GTCTCGTGGGCTCGGAGATGTGTATAAGAGACAG"
    read2_sequence = "GTCTCGTGGGCTCGGAGATGTGTATAAGAGACAG"
    t7_sequence = "TAATACGACTCACTATAGGG"
    sequence_candidates = [
        {
            "candidate_id": "seq_n7",
            "block_id": "block_seq_n7",
            "normalized_sequence": n7_sequence,
            "direction": "5_to_3",
            "nearby_text": "P7 primer with i7 index for sci-RNA-seq library amplification",
            "name_hint": "Nextera N7 index primer",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "PCR", "quote": n7_sequence},
        },
        {
            "candidate_id": "seq_library_p7",
            "block_id": "block_seq_library_p7",
            "normalized_sequence": library_p7_sequence,
            "direction": "5_to_3",
            "nearby_text": "P7 library index primer",
            "name_hint": "Library P7 Index",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "PCR", "quote": library_p7_sequence},
        },
        {
            "candidate_id": "seq_t7",
            "block_id": "block_seq_t7",
            "normalized_sequence": t7_sequence,
            "direction": "5_to_3",
            "nearby_text": "T7 promoter sequence",
            "name_hint": "T7 promoter",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "Primer", "quote": t7_sequence},
        },
    ]
    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [],
        "assembled_component_edges": [
            {
                "component_memory_id": "cmem_short_p7",
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "Short P7 prefix",
                "component_role": "adapter",
                "component_sequence": "CAAGCAGAAGACGGCAT",
                "protocol_count": 1,
            },
            {
                "component_memory_id": "cmem_p7",
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "Illumina P7 adapter",
                "component_role": "adapter",
                "component_sequence": "CAAGCAGAAGACGGCATACGAGAT",
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_s7",
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "Nextera N7xx primer entry point (s7)",
                "component_role": "primer_site",
                "component_sequence": "GTCTCGTGGGCTCGG",
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_library_p7_adapter",
                "parent_oligo_name": "Library P7 Index",
                "parent_sequence": library_p7_sequence,
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "Illumina P7 adapter",
                "component_role": "adapter",
                "component_sequence": "CAAGCAGAAGACGGCATACGAGAT",
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_library_read2",
                "parent_oligo_name": "Library P7 Index",
                "parent_sequence": library_p7_sequence,
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "Illumina Nextera Read 2 primer",
                "component_role": "primer",
                "component_sequence": read2_sequence,
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_read2_s7",
                "parent_oligo_name": "Illumina Nextera Read 2 primer",
                "parent_sequence": read2_sequence,
                "parent_direction": "5_to_3",
                "component_order": 1,
                "component_name": "Nextera N7xx primer entry point (s7)",
                "component_role": "primer_site",
                "component_sequence": "GTCTCGTGGGCTCGG",
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_read2_me",
                "parent_oligo_name": "Illumina Nextera Read 2 primer",
                "parent_sequence": read2_sequence,
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "Nextera Tn5 binding site (19-bp Mosaic End (ME))",
                "component_role": "tn5_binding_site",
                "component_sequence": "AGATGTGTATAAGAGACAG",
                "protocol_count": 3,
            },
            {
                "component_memory_id": "cmem_pe1",
                "parent_oligo_name": "Acrydite-modified primer",
                "parent_sequence": f"GGGG{t7_sequence}CTCTTTCCCTACACGACGCTCTTC",
                "parent_direction": "5_to_3",
                "component_order": 2,
                "component_name": "PE1 adapter",
                "component_role": "adapter",
                "component_sequence": "CTCTTTCCCTACACGACGCTCTTC",
                "protocol_count": 1,
            },
        ],
    }
    prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.txt"],
        [
            {"name": "Nextera N7 index primer", "candidate_id": "seq_n7", "role": "primer", "kind": "assembled"},
            {"name": "Library P7 Index", "candidate_id": "seq_library_p7", "role": "primer", "kind": "assembled"},
            {"name": "T7 promoter", "candidate_id": "seq_t7", "role": "promoter", "kind": "single"},
        ],
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}
    n7_parent = by_name[display_name_key("Nextera N7 index primer")]
    component_names = {component.name for component in n7_parent.components}

    assert "Illumina P7 adapter" in component_names
    assert "Nextera N7xx primer entry point (s7)" in component_names
    assert "Short P7 prefix" not in component_names
    assert display_name_key("Illumina P7 adapter") in by_name
    assert display_name_key("Nextera N7xx primer entry point (s7)") in by_name
    assert display_name_key("Short P7 prefix") not in by_name
    assert display_name_key("PE1 adapter") not in by_name
    library_parent = by_name[display_name_key("Library P7 Index")]
    library_component_names = {component.name for component in library_parent.components}
    assert "Illumina P7 adapter" in library_component_names
    assert "Illumina Nextera Read 2 primer" not in library_component_names
    assert display_name_key("Illumina Nextera Read 2 primer") not in by_name


def test_component_memory_prompt_is_parent_to_child_and_source_scoped() -> None:
    n7_sequence = "CAAGCAGAAGACGGCATACGAGAT[i7]GTCTCGTGGGCTCGG"
    memory = {
        "assembled_component_edges": [
            {
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "component_order": 1,
                "component_name": "Short P7 prefix",
                "component_role": "adapter",
                "component_sequence": "CAAGCAGAAGACGGCAT",
                "protocol_count": 1,
            },
            {
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "component_order": 1,
                "component_name": "Illumina P7 adapter",
                "component_role": "adapter",
                "component_sequence": "CAAGCAGAAGACGGCATACGAGAT",
                "protocol_count": 2,
            },
            {
                "parent_oligo_name": "Nextera N7 index primer",
                "parent_sequence": n7_sequence,
                "component_order": 2,
                "component_name": "Nextera N7xx primer entry point (s7)",
                "component_role": "primer_site",
                "component_sequence": "GTCTCGTGGGCTCGG",
                "protocol_count": 2,
            },
            {
                "parent_oligo_name": "Illumina P7 adapter",
                "parent_sequence": "CAAGCAGAAGACGGCATACGAGAT",
                "component_order": 1,
                "component_name": "ATAC Primer Mix reverse",
                "component_role": "primer",
                "component_sequence": "CAAGCAGAAGACGGCAT",
                "protocol_count": 1,
            },
            {
                "parent_oligo_name": "Acrydite-modified primer",
                "parent_sequence": "GGGGTAATACGACTCACTATAGGGCTCTTTCCCTACACGACGCTCTTC",
                "component_order": 2,
                "component_name": "PE1 adapter",
                "component_role": "adapter",
                "component_sequence": "CTCTTTCCCTACACGACGCTCTTC",
                "protocol_count": 1,
            },
        ]
    }
    prompt = component_memory_prompt_tsv(
        memory,
        source_text_key=display_name_key(f"sci-RNA-seq P7 primer {n7_sequence}"),
        sequence_candidates=[{"normalized_sequence": n7_sequence, "name_hint": "P7 primer"}],
    )

    assert "Nextera N7 index primer" in prompt
    assert "Illumina P7 adapter" in prompt
    assert "Nextera N7xx primer entry point (s7)" in prompt
    assert "Short P7 prefix" not in prompt
    assert "ATAC Primer Mix reverse" not in prompt
    assert "Acrydite-modified primer" not in prompt
    assert "PE1 adapter" not in prompt


def test_protocol_brief_routes_same_family_memory_completion() -> None:
    def candidate(candidate_id: str, sequence: str) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "block_id": f"block_{candidate_id}",
            "normalized_sequence": sequence,
            "direction": "5_to_3",
            "nearby_text": sequence,
            "name_hint": "Parent primer",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "Primers", "quote": sequence},
        }

    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": "mem_parent",
                "name": "Barcoded RT primer variant 1",
                "sequence": "ACGTACGTACGTACGTACGT",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
                "source_protocol_ids": ["cel_seq2"],
                "source_protocol_names": ["CEL-seq2"],
            },
            {
                "memory_id": "mem_t7",
                "name": "T7 promoter",
                "sequence": "TAATACGACTCACTATAGGG",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
                "source_protocol_ids": ["cel_seq2"],
                "source_protocol_names": ["CEL-seq2"],
            },
        ],
    }
    links = [{"name": "Barcoded RT primer variant 1", "candidate_id": "seq_parent", "role": "primer", "kind": "single"}]
    sequence_candidates = [candidate("seq_parent", "ACGTACGTACGTACGTACGT")]

    sci_prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.txt"],
        links,
        sequence_candidates,
        memory,
        {"summary": "sci-RNA-seq profiles single-cell transcriptomes with combinatorial indexing.", "major_steps": []},
    )
    cel_prediction = resolve_oligos(
        "fixture_protocol",
        "Fixture Protocol",
        "train",
        ["fixture.txt"],
        links,
        sequence_candidates,
        memory,
        {"summary": "CEL-seq measures single-cell gene expression with early barcoding and pooled amplification.", "major_steps": []},
    )

    assert display_name_key("T7 promoter") not in {display_name_key(oligo.name) for oligo in sci_prediction.oligos}
    assert display_name_key("T7 promoter") in {display_name_key(oligo.name) for oligo in cel_prediction.oligos}


def test_resolver_collapses_barcoded_series_and_agentic_illumina_memory() -> None:
    def candidate(candidate_id: str, sequence: str) -> dict[str, object]:
        return {
            "candidate_id": candidate_id,
            "block_id": f"block_{candidate_id}",
            "normalized_sequence": sequence,
            "direction": "unknown",
            "nearby_text": sequence,
            "name_hint": f"Sequence candidate {candidate_id}",
            "evidence": {"source_id": "fixture.txt", "page": 1, "section": "Primers", "quote": sequence},
        }

    protocol_rows = [
        ("mem_variant1", "Barcoded RT primer variant 1", "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATC[8-bp cell barcode]TTTTTTTTTTTTTTTTTTTTTTTTV"),
        ("mem_variant2", "Barcoded RT primer variant 2", "GCCGGTAATACGACTCACTATAGGGAGTTCTACAGTCCGACGATC[6-bp UMI][6-bp cell barcode]TTTTTTTTTTTTTTTTTTTTTTTTV"),
        ("mem_ra3", "Illumina RA3 adapter (TruSeq Small RNA kit)", "TGGAATTCTCGGGTGCCAAGG"),
        ("mem_rp1", "Illumina RP1 primer (TruSeq Small RNA kit)", "AATGATACGGCGACCACCGAGATCTACACGTTCAGAGTTCTACAGTCCGA"),
        ("mem_rpi", "Illumina RPI primers", "CAAGCAGAAGACGGCATACGAGAT[6-bp RPI]GTGACTGGAGTTCCTTGGCACCCGAGAATTCCA"),
        ("mem_rtp", "Illumina RTP primer (TruSeq Small RNA kit)", "GCCTTGGCACCCGAGAATTCCA"),
        ("mem_t7", "T7 promoter", "TAATACGACTCACTATAGGG"),
    ]
    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": memory_id,
                "name": name,
                "sequence": sequence,
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": [],
                "source_protocol_ids": ["cel_seq2"],
                "source_protocol_names": ["CEL-seq2"],
            }
            for memory_id, name, sequence in protocol_rows
        ],
    }
    sequences = [
        "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATCCATCACGCTTTTTTTTTTTTTTTTTTTTTTTTV",
        "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATCGTCGTCGCTTTTTTTTTTTTTTTTTTTTTTTTV",
        "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATCACGACCGCTTTTTTTTTTTTTTTTTTTTTTTTV",
        "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATCTGATGCGCTTTTTTTTTTTTTTTTTTTTTTTTV",
    ]
    sequence_candidates = [candidate(f"seq_{index:04d}", sequence) for index, sequence in enumerate(sequences, start=1)]
    links = [
        {
            "name": f"CEL-Seq barcoded RT primer #{index}",
            "candidate_id": f"seq_{index:04d}",
            "role": "primer",
            "kind": "assembled",
        }
        for index in range(1, 5)
    ]
    links.extend(
        [
            {"name": "RNA RT Primer (RTP, from Illumina kit)", "candidate_id": None, "role": "primer", "kind": "single"},
            {"name": "RNA PCR Primer (RP1, from Illumina kit)", "candidate_id": None, "role": "primer", "kind": "single"},
            {"name": "uniquely indexed RNA PCR Primer (RPIX, from Illumina kit)", "candidate_id": None, "role": "primer", "kind": "single"},
            {"name": "3' adapter (RA3, from Illumina kit)", "candidate_id": None, "role": "adapter", "kind": "single"},
        ]
    )

    prediction = resolve_oligos(
        "cel_seq",
        "CEL-seq",
        "train",
        ["fixture.txt"],
        links,
        sequence_candidates,
        memory,
    )
    by_name = {display_name_key(oligo.name): oligo for oligo in prediction.oligos}

    assert display_name_key("CEL-Seq barcoded RT primer #1") not in by_name
    assert by_name[display_name_key("Barcoded RT primer variant 1")].sequence == (
        "CGATTGAGGCCGGTAATACGACTCACTATAGGGGTTCAGAGTTCTACAGTCCGACGATCBBBBBBBBTTTTTTTTTTTTTTTTTTTTTTTTV"
    )
    assert by_name[display_name_key("Barcoded RT primer variant 1")].components[0].role == "cell_barcode"
    assert by_name[display_name_key("Barcoded RT primer variant 2")].sequence_source == "memory_completed"
    assert by_name[display_name_key("Illumina RTP primer (TruSeq Small RNA kit)")].memory_id == "mem_rtp"
    assert by_name[display_name_key("Illumina RP1 primer (TruSeq Small RNA kit)")].memory_id == "mem_rp1"
    assert by_name[display_name_key("Illumina RPI primers")].memory_id == "mem_rpi"
    assert by_name[display_name_key("Illumina RA3 adapter (TruSeq Small RNA kit)")].memory_id == "mem_ra3"
    assert by_name[display_name_key("T7 promoter")].memory_id == "mem_t7"


def test_improve_creates_prediction_and_trace(tmp_path: Path) -> None:
    protocol_dir, _gt_path, _root = write_fixture_protocol(tmp_path)
    out = tmp_path / "training" / "fixture_protocol"
    result = runner.invoke(
        app,
        [
        "improve",
            "--protocol-id",
            "fixture_protocol",
            "--input",
            str(protocol_dir),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Predicted oligos" in result.output
    run_dir = out
    assert (run_dir / "chunks.json").exists()
    assert (run_dir / "prediction.json").exists()
    assert (run_dir / "trace.json").exists()
    for filename in [
        "comparison.json",
        "human_review.md",
        "human_review.tsv",
        "failure_package.json",
        "trace_store.jsonl",
    ]:
        assert not (run_dir / filename).exists(), filename
    prediction = json.loads((run_dir / "prediction.json").read_text(encoding="utf-8"))
    trace = json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))
    assert "summary" in prediction
    assert "major_steps" in prediction
    assert "technical_summary" not in prediction
    assert all("protocol_id" not in oligo and "protocol_name" not in oligo for oligo in prediction["oligos"])
    assert prediction["oligos"]
    assert trace["use_memory"] is False
    assert trace["memory_source"] == "disabled"
    assert "ground_truth_json" not in trace
    assert "metrics" not in trace
    assert "prompt" in trace["linker"]
    assert "context_blocks" in trace["linker"]
    assert trace["chunks_path"] == str(run_dir / "chunks.json")
    chunks = json.loads((run_dir / "chunks.json").read_text(encoding="utf-8"))
    assert chunks["chunk_index"]
    assert chunks["chunks"]
    assert all(oligo["evidence"] for oligo in prediction["oligos"] if oligo.get("sequence_source") == "explicit_in_protocol")


def test_codex_linker_can_add_chunk_extracted_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protocol_dir, _gt_path, _root = write_fixture_protocol(tmp_path)
    out = tmp_path / "training" / "fixture_protocol"
    monkeypatch.setenv(
        "CDNA_TEST_CODEX_RESPONSE",
        json.dumps(
            {
                "protocol_brief": {
                    "summary": "Fixture Protocol tests extraction of explicit primer and adapter oligos.",
                    "major_steps": ["Read source chunks", "Link visible oligo sequence", "Write prediction"],
                    "technical_summary": "Ignored legacy field.",
                    "ignored_extra_key": "not copied",
                },
                "links": [
                    {
                        "mention_id": None,
                        "name": "Codex Added Primer",
                        "candidate_id": "codex_seq_0001",
                        "role": "primer",
                        "kind": "single",
                        "notes": "Extracted from provided source chunk.",
                    }
                ],
                "additional_sequence_candidates": [
                    {
                        "candidate_id": "codex_seq_0001",
                        "name": "Codex Added Primer",
                        "raw_sequence": "5'- TTTTCCCCAAAAGGGG -3'",
                        "direction": "5_to_3",
                        "block_id": "block_00001",
                        "quote": "Codex Added Primer: 5'- TTTTCCCCAAAAGGGG -3'",
                    }
                ],
                "review_flags": [],
            }
        ),
    )
    result = runner.invoke(
        app,
        [
            "improve",
            "--protocol-id",
            "fixture_protocol",
            "--input",
            str(protocol_dir),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    prediction = json.loads((out / "prediction.json").read_text(encoding="utf-8"))
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert trace["linker"]["status"] == "codex_linked"
    assert trace["linker"]["codex_call_count"] == 1
    assert "source_context_chunk_refs" in trace["linker"]["prompt"]
    assert "requested_chunk_ids" not in trace["linker"]["prompt"]
    assert trace["linker"]["prompt_char_count"] == len(trace["linker"]["prompt"])
    assert trace["linker"]["prompt_sequence_candidate_count"] <= trace["linker"]["total_sequence_candidate_count"]
    assert trace["linker"]["chunk_manifest_count"] >= 1
    assert trace["linker"]["context_chunks"]
    assert trace["linker"]["protocol_brief"]["summary"] == "Fixture Protocol tests extraction of explicit primer and adapter oligos."
    assert "technical_summary" not in trace["linker"]["protocol_brief"]
    assert trace["linker"]["protocol_brief"]["major_steps"] == ["Link visible oligo sequence"]
    assert any(candidate["candidate_id"] == "codex_seq_0001" for candidate in trace["sequence_candidates"])
    assert prediction["summary"] == "Fixture Protocol tests extraction of explicit primer and adapter oligos."
    assert prediction["major_steps"] == ["Link visible oligo sequence"]
    assert "technical_summary" not in prediction
    assert "ignored_extra_key" not in prediction
    assert prediction["oligos"][0]["name"] == "Codex Added Primer"
    assert prediction["oligos"][0]["sequence"] == "TTTTCCCCAAAAGGGG"


def test_codex_null_memory_link_requires_source_support() -> None:
    memory = {
        "source": "fixture",
        "source_format": "tsv",
        "protocol_nodes": [],
        "edges": [],
        "oligo_nodes": [
            {
                "memory_id": "mem_p5",
                "name": "Illumina P5 adapter",
                "sequence": "AATGATACGGCGACCACCGAGATCTACAC",
                "direction": "5_to_3",
                "allowed_for_memory_completion": True,
                "aliases": ["P5 adapter"],
            }
        ],
    }

    unsupported = sanitize_links(
        [{"name": "Illumina P5 adapter", "candidate_id": None, "role": "adapter", "kind": "single"}],
        [],
        [],
        memory,
        source_text_key=display_name_key("This technique uses split-pool barcoding but does not mention the adapter name."),
    )
    supported = sanitize_links(
        [{"name": "Illumina P5 adapter", "candidate_id": None, "role": "adapter", "kind": "single"}],
        [],
        [],
        memory,
        source_text_key=display_name_key("The protocol explicitly uses a P5 adapter during PCR."),
    )

    assert unsupported == []
    assert supported[0]["name"] == "Illumina P5 adapter"


def test_eval_test_requires_frozen(tmp_path: Path) -> None:
    write_fixture_protocol(tmp_path, split="test")
    result = runner.invoke(app, ["eval", "test", str(tmp_path)])
    assert result.exit_code != 0
    assert "requires --frozen" in result.output


def test_eval_accepts_explicit_memory_path(tmp_path: Path) -> None:
    write_fixture_protocol(tmp_path, split="eval")
    memory_dir, memory_path = write_fixture_memory(tmp_path)
    out = tmp_path / "eval-runs"
    result = runner.invoke(
        app,
        [
            "eval",
            "eval",
            str(tmp_path),
            "--out",
            str(out),
            "--use-memory",
            "--memory-path",
            str(memory_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Split: eval" in result.output
    assert "Running 1/1: fixture_protocol" in result.output
    assert "done fixture_protocol:" in result.output
    assert "Aggregate:" in result.output
    assert "Summary JSON:" in result.output

    summary_path = next(out.glob("*/eval_summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["use_memory"] is True
    assert summary["memory_path"] == str(memory_dir.resolve())
    assert summary["aggregate"]["metrics"]["oligo_name_recall"]["denominator"] == 2
    assert "failure_count" in summary["aggregate"]
    assert Path(summary["summary_markdown"]).exists()
    assert (out / "latest_summary.json").exists()
    assert (out / "latest_summary.md").exists()

    run_dir = Path(summary["results"][0]["run_dir"])
    trace = json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["memory_source"] == str(memory_path)
    assert trace["memory"]["leave_one_out"]["excluded_current_protocol_rows"] == 2


def test_eval_uses_protocol_split_file(tmp_path: Path) -> None:
    write_fixture_protocol(tmp_path, split="eval")
    other_dir = tmp_path / "protocols" / "other_protocol"
    other_dir.mkdir(parents=True)
    (other_dir / "protocol.txt").write_text("Other Primer: 5'- ACGTACGTACGT -3'\n", encoding="utf-8")
    (other_dir / "groundtruth_oligos.json").write_text(
        json.dumps(
            {
                "protocol_id": "other_protocol",
                "protocol_name": "Other Protocol",
                "oligos": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    split_file = tmp_path / "protocol_split.tsv"
    split_file.write_text(
        "Split\tprotocol_name\n"
        "eval\tFixture Protocol\n"
        "train\tOther Protocol\n",
        encoding="utf-8",
    )
    out = tmp_path / "eval-runs"
    result = runner.invoke(app, ["eval", "eval", str(tmp_path), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Split file:" in result.output
    assert "Running 1/1: fixture_protocol" in result.output
    assert "other_protocol" not in result.output

    summary_path = next(out.glob("*/eval_summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["split_file"] == str(split_file.resolve())
    assert summary["count"] == 1
    assert [item["protocol_id"] for item in summary["results"]] == ["fixture_protocol"]


def test_eval_requires_memory_path_when_memory_enabled(tmp_path: Path) -> None:
    write_fixture_protocol(tmp_path, split="eval")
    result = runner.invoke(app, ["eval", "eval", str(tmp_path), "--use-memory"])
    assert result.exit_code != 0
    assert "--memory-path is required" in result.output


def test_chunks_command_prints_blocks(tmp_path: Path) -> None:
    protocol_dir, _gt_path, _root = write_fixture_protocol(tmp_path)
    result = runner.invoke(app, ["chunks", str(protocol_dir)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["blocks"]
    assert any(block["block_type"] == "sequence_diagram" for block in payload["blocks"])


def test_improve_requires_memory_path_when_enabled(tmp_path: Path) -> None:
    protocol_dir, _gt_path, _root = write_fixture_protocol(tmp_path)
    out = tmp_path / "training" / "fixture_protocol"
    result = runner.invoke(
        app,
        [
            "improve",
            "--protocol-id",
            "fixture_protocol",
            "--input",
            str(protocol_dir),
            "--out",
            str(out),
            "--use-memory",
        ],
    )
    assert result.exit_code != 0
    assert "--memory-path is required" in result.output


def write_fixture_memory(root: Path) -> tuple[Path, Path]:
    memory_dir = root / "memory" / "agent_memory"
    memory_dir.mkdir(parents=True)
    memory_path = memory_dir / "seed.tsv"
    memory_path.write_text(
        "\n".join(
            [
                "memory_id\toligo_name\tdirection\toligo_sequence\tprotocol_count\tsource_protocol_ids\tsource_protocol_names\taliases\tcustom_note",
                "mem_shared\tShared Primer\t5_to_3\tACGTACGTACGT\t1\tother_protocol\tOther Protocol\tShared oligo\tshared-row",
                "mem_mixed\tMixed Source Primer\t5_to_3\tGGGGAAAACCCC\t2\tfixture_protocol;other_protocol\tFixture Protocol;Other Protocol\t\tmixed-row",
                "mem_current\tCurrent Only Primer\t5_to_3\tTTTTCCCCAAAA\t1\tfixture_protocol\tFixture Protocol\t\texcluded-row",
                "mem_ambiguous_a\tAmbiguous Primer\t5_to_3\tAAAAAAAAAAAA\t1\tother_protocol\tOther Protocol\t\tambiguous-a",
                "mem_ambiguous_b\tAmbiguous Primer\t5_to_3\tCCCCCCCCCCCC\t1\tthird_protocol\tThird Protocol\t\tambiguous-b",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return memory_dir, memory_path


def test_tsv_memory_directory_path_filters_and_preserves_header(tmp_path: Path) -> None:
    memory_dir, memory_path = write_fixture_memory(tmp_path)
    (memory_dir / "assembled_oligo_component_memory.tsv").write_text(
        "\n".join(
            [
                "component_memory_id\tparent_oligo_id\tparent_oligo_name\tparent_role\tparent_kind\tparent_direction\tparent_sequence\tcomponent_order\tcomponent_name\tcomponent_role\tcomponent_sequence\tprotocol_count\tsource_protocol_ids\tsource_protocol_names",
                "cmem_shared\toligo_parent\tParent Primer\tprimer\tassembled\t5_to_3\tACGTACGTACGT\t1\tShared Component\tprimer\tACGTACGT\t1\tother_protocol\tOther Protocol",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_runtime_memory(memory_dir, use_memory=True)
    filtered = filter_memory_for_protocol(loaded, "fixture_protocol", "train")
    lookup = memory_by_name(filtered)
    prompt = memory_prompt_tsv(filtered)

    assert loaded["source"] == str(memory_path)
    assert loaded["component_memory_source"] == str(memory_dir / "assembled_oligo_component_memory.tsv")
    assert len(loaded["assembled_component_edges"]) == 1
    assert loaded["memory_columns"][-1] == "custom_note"
    assert filtered["leave_one_out"]["excluded_current_protocol_rows"] == 2
    assert filtered["leave_one_out"]["excluded_current_protocol_only_rows"] == 1
    assert filtered["leave_one_out"]["excluded_current_protocol_mixed_source_rows"] == 1
    assert lookup[display_name_key("Shared Primer")]["sequence"] == "ACGTACGTACGT"
    assert display_name_key("Current Only Primer") not in lookup
    assert lookup[display_name_key("Mixed Source Primer")]["sequence"] == "GGGGAAAACCCC"
    assert lookup[display_name_key("Mixed Source Primer")]["source_protocol_ids"] == ["other_protocol"]
    assert display_name_key("Ambiguous Primer") not in lookup
    assert "custom_note" in prompt
    assert "shared-row" in prompt
    assert "excluded-row" not in prompt
    assert "mixed-row" in prompt


def test_improve_uses_tsv_memory_directory_without_ground_truth_leakage(tmp_path: Path) -> None:
    protocol_id = "fixture_protocol"
    protocol_dir = tmp_path / "protocols" / protocol_id
    protocol_dir.mkdir(parents=True)
    (protocol_dir / "protocol.txt").write_text(
        "\n".join(
            [
                "Shared Primer: used during amplification.",
                "Mixed Source Primer: used during indexing.",
                "Current Only Primer: used during reverse transcription.",
                "Ambiguous Primer: used during indexing.",
            ]
        ),
        encoding="utf-8",
    )
    memory_dir, memory_path = write_fixture_memory(tmp_path)
    out = tmp_path / "training" / protocol_id
    result = runner.invoke(
        app,
        [
            "improve",
            "--protocol-id",
            protocol_id,
            "--input",
            str(protocol_dir),
            "--out",
            str(out),
            "--use-memory",
            "--memory-path",
            str(memory_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    prediction = json.loads((out / "prediction.json").read_text(encoding="utf-8"))
    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    by_name = {display_name_key(oligo["name"]): oligo for oligo in prediction["oligos"]}

    assert trace["memory_source"] == str(memory_path)
    assert trace["memory"]["leave_one_out"]["excluded_current_protocol_rows"] == 2
    assert trace["memory"]["leave_one_out"]["excluded_current_protocol_mixed_source_rows"] == 1
    assert "training_oligo_memory_tsv" in trace["linker"]["prompt"]
    assert "custom_note" in trace["linker"]["prompt"]
    assert by_name[display_name_key("Shared Primer")]["sequence_source"] == "memory_completed"
    assert by_name[display_name_key("Shared Primer")]["sequence"] == "ACGTACGTACGT"
    assert by_name[display_name_key("Mixed Source Primer")]["sequence_source"] == "memory_completed"
    assert by_name[display_name_key("Mixed Source Primer")]["sequence"] == "GGGGAAAACCCC"
    assert display_name_key("Current Only Primer") not in by_name
    assert display_name_key("Ambiguous Primer") not in by_name


def test_runtime_memory_build_uses_explicit_folder(tmp_path: Path) -> None:
    write_fixture_protocol(tmp_path, split="train")
    memory = build_runtime_memory(tmp_path)
    protocol_ids = {node["protocol_id"] for node in memory["protocol_nodes"]}
    assert protocol_ids == {"fixture_protocol"}
    assert memory["source"] == "explicit_ground_truth_folder"
