"""Symbolic strand operations. Top is 5′→3′; bottom is 3′→5′."""

from copy import deepcopy
from uuid import uuid4

from .molecule import MoleculeState, symbolic

OPERATIONS = {"reverse_transcribe": "reverse_transcription",
              "template_switch": "template_switching"}


def product(substrate, label, strands):
    return MoleculeState.model_validate({
        "id": f"S_{uuid4().hex[:12]}", "label": label, "strands": strands,
        "origin": "skill", "evidence": list(substrate["evidence"]),
        "skill_call_id": None, "review_status": "unreviewed",
        "stale_since_revision": None,
    }).model_dump()


def reverse_transcribe(rna_state: dict, primer: str) -> dict:
    symbolic(primer)
    strands = deepcopy(rna_state["strands"])
    if not strands["top"] or strands["bottom"]:
        raise ValueError("reverse_transcribe needs single-stranded RNA on top")
    if not any(s["type"] == "insert" for s in strands["top"]):
        raise ValueError("RNA substrate must contain an insert segment")
    # Complementary segments retain their left-to-right alignment. The primer
    # is at the cDNA 5′ end (right); its poly-T region pairs with the RNA tail.
    strands["bottom"] = [
        {**s, "origin": "skill"} for s in strands["top"] if s["type"] != "polyA"
    ] + [{"name": primer, "type": "primer", "origin": "skill"}]
    return product(rna_state, "RNA/cDNA hybrid", strands)


def template_switch(hybrid: dict, tso: str) -> dict:
    symbolic(tso)
    strands = deepcopy(hybrid["strands"])
    if not strands["top"] or not strands["bottom"]:
        raise ValueError("template_switch needs a two-strand RNA/cDNA hybrid")
    # Extension of cDNA at its 3′ end is on the LEFT in the bottom-strand view.
    strands["bottom"].insert(0, {"name": tso, "type": "tso", "origin": "skill"})
    return product(hybrid, "RNA/cDNA hybrid with TSO-derived handle", strands)


SKILLS = {"reverse_transcribe": reverse_transcribe, "template_switch": template_switch}
