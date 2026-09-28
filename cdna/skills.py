"""Pure symbolic strand operations. Top is 5′→3′; bottom is 3′→5′."""

from copy import deepcopy
from hashlib import sha256
import json
from typing import overload

from pydantic import TypeAdapter

from .molecule import MoleculeState, Text

OPERATIONS = {"reverse_transcribe": "reverse_transcription",
              "template_switch": "template_switching"}

_OLIGO = TypeAdapter(Text)


def _state(value: MoleculeState | dict) -> dict:
    # Revalidate model instances too: callers can mutate their lists and fields.
    data = value.model_dump() if isinstance(value, MoleculeState) else value
    return MoleculeState.model_validate(data).model_dump()


def _product(substrate: dict, operation: str, oligo: str, label: str, strands: dict) -> dict:
    inputs = json.dumps([operation, substrate, oligo], sort_keys=True, separators=(",", ":"))
    return MoleculeState.model_validate({
        "id": f"S_{sha256(inputs.encode()).hexdigest()[:24]}",
        "label": label, "strands": strands,
        "origin": "skill", "evidence": list(substrate["evidence"]),
        "skill_call_id": None, "review_status": "unreviewed",
        "stale_since_revision": None,
    }).model_dump()


def _check_common(substrate: dict, result: dict) -> None:
    if result["strands"]["top"] != substrate["strands"]["top"]:
        raise ValueError("Product must preserve the top strand")
    if result["evidence"] != substrate["evidence"]:
        raise ValueError("Product must preserve substrate evidence")
    if (result["origin"] != "skill" or result["skill_call_id"] is not None
            or result["review_status"] != "unreviewed"
            or result["stale_since_revision"] is not None):
        raise ValueError("Product must be an uncommitted, unreviewed skill state")
    if result["id"] == substrate["id"]:
        raise ValueError("Product must have a new state ID")


def check_reverse_transcribe(
    rna_state: MoleculeState | dict, primer: str, result: MoleculeState | dict,
) -> None:
    """Raise ValueError if the product violates reverse-transcription invariants."""
    substrate, product = _state(rna_state), _state(result)
    primer = _OLIGO.validate_python(primer, strict=True)
    _check_common(substrate, product)
    bottom = product["strands"]["bottom"]
    if not bottom or bottom[-1] != {"name": primer, "type": "primer", "origin": "skill"}:
        raise ValueError("RT primer must be at the bottom strand's right (5′) end")
    template = [s for s in substrate["strands"]["top"] if s["type"] != "polyA"]
    if len(bottom) != len(template) + 1:
        raise ValueError("cDNA must copy every non-polyA template segment once")
    for source, copied in zip(template, bottom[:-1]):
        if (copied["name"], copied["type"], copied["origin"]) != (source["name"], source["type"], "skill"):
            raise ValueError("cDNA segments must retain template order and skill provenance")


def check_template_switch(
    hybrid: MoleculeState | dict, tso: str, result: MoleculeState | dict,
) -> None:
    """Raise ValueError if the product violates template-switching invariants."""
    substrate, product = _state(hybrid), _state(result)
    tso = _OLIGO.validate_python(tso, strict=True)
    _check_common(substrate, product)
    bottom = product["strands"]["bottom"]
    if not bottom or bottom[0] != {"name": tso, "type": "tso", "origin": "skill"}:
        raise ValueError("TSO handle must be at the bottom strand's left (3′) end")
    if bottom[1:] != substrate["strands"]["bottom"]:
        raise ValueError("Template switching must preserve the existing cDNA strand")


@overload
def reverse_transcribe(rna_state: MoleculeState, primer: str) -> MoleculeState: ...


@overload
def reverse_transcribe(rna_state: dict, primer: str) -> dict: ...


def reverse_transcribe(rna_state: MoleculeState | dict, primer: str) -> MoleculeState | dict:
    """Copy a symbolic RNA template, placing the RT primer at cDNA's 5′ end."""
    substrate = _state(rna_state)
    primer = _OLIGO.validate_python(primer, strict=True)
    strands = deepcopy(substrate["strands"])
    if not strands["top"] or strands["bottom"]:
        raise ValueError("reverse_transcribe needs single-stranded RNA on top")
    if not any(s["type"] == "insert" for s in strands["top"]):
        raise ValueError("RNA substrate must contain an insert segment")
    # The primer lies at the cDNA 5′ end (right), pairing with the RNA tail.
    strands["bottom"] = [
        {**s, "origin": "skill"} for s in strands["top"] if s["type"] != "polyA"
    ] + [{"name": primer, "type": "primer", "origin": "skill"}]
    result = _product(substrate, "reverse_transcribe", primer, "RNA/cDNA hybrid", strands)
    check_reverse_transcribe(substrate, primer, result)
    return MoleculeState.model_validate(result) if isinstance(rna_state, MoleculeState) else result


@overload
def template_switch(hybrid: MoleculeState, tso: str) -> MoleculeState: ...


@overload
def template_switch(hybrid: dict, tso: str) -> dict: ...


def template_switch(hybrid: MoleculeState | dict, tso: str) -> MoleculeState | dict:
    """Extend the cDNA 3′ end with a symbolic TSO-derived handle."""
    substrate = _state(hybrid)
    tso = _OLIGO.validate_python(tso, strict=True)
    strands = deepcopy(substrate["strands"])
    if not strands["top"] or not strands["bottom"]:
        raise ValueError("template_switch needs a two-strand RNA/cDNA hybrid")
    # Extension is on the LEFT in the bottom-strand 3′→5′ view.
    strands["bottom"].insert(0, {"name": tso, "type": "tso", "origin": "skill"})
    result = _product(substrate, "template_switch", tso,
                      "RNA/cDNA hybrid with TSO-derived handle", strands)
    check_template_switch(substrate, tso, result)
    return MoleculeState.model_validate(result) if isinstance(hybrid, MoleculeState) else result


SKILLS = {"reverse_transcribe": reverse_transcribe, "template_switch": template_switch}
