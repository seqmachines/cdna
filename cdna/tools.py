"""Shared tool schemas and dispatch for cDNA and hosts mounting its skills."""

from .molecule import Contract, MoleculeState, Text
from .skills import SKILLS


class ReverseTranscribe(Contract):
    rna_state: MoleculeState
    primer: Text


class TemplateSwitch(Contract):
    hybrid: MoleculeState
    tso: Text


ARGUMENTS = {"reverse_transcribe": ReverseTranscribe, "template_switch": TemplateSwitch}
INPUT_NAMES = {"reverse_transcribe": ("rna_state", "primer"), "template_switch": ("hybrid", "tso")}
DESCRIPTIONS = {
    "reverse_transcribe": "Construct a symbolic RNA/cDNA hybrid from single-stranded RNA and a named primer. Returns an uncommitted state; top is 5′→3′ and bottom is 3′→5′.",
    "template_switch": "Extend the cDNA 3′ end of a symbolic RNA/cDNA hybrid with a named TSO-derived handle. Returns an uncommitted state, with the handle at the left of the bottom (3′→5′) row.",
}


def tool_definitions():
    return [{"name": name, "description": DESCRIPTIONS[name],
             "inputSchema": model.model_json_schema()} for name, model in ARGUMENTS.items()]


def call_skill(name: str, arguments: dict) -> dict:
    if name not in ARGUMENTS:
        raise ValueError(f"Unknown cDNA skill: {name}")
    values = ARGUMENTS[name].model_validate(arguments).model_dump()
    return SKILLS[name](**values)
