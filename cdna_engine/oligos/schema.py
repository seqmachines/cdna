from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


Split = Literal["train", "eval", "test"]
Role = Literal[
    "primer",
    "adapter",
    "oligo",
    "primer_site",
    "tn5_binding_site",
    "probe",
    "promoter",
    "unknown",
]
Kind = Literal["single", "assembled", "double_stranded", "hairpin"]
Direction = Literal["5_to_3", "3_to_5", "unknown"]
SequenceSource = Literal[
    "explicit_in_protocol",
    "explicit_in_linked_table",
    "memory_completed",
    "curated_ground_truth",
    "not_shown_in_protocol",
    "unknown",
]


class Evidence(BaseModel):
    source_id: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None


class OligoComponent(BaseModel):
    order: int = 0
    name: str
    sequence: str | None = None
    role: str | None = None


class Oligo(BaseModel):
    oligo_id: str
    protocol_id: str = ""
    protocol_name: str = ""

    name: str
    aliases: list[str] = Field(default_factory=list)
    role: Role

    kind: Kind

    sequence: str | None = None
    direction: Direction = "5_to_3"

    components: list[OligoComponent] = Field(default_factory=list)

    sequence_source: SequenceSource = "unknown"

    memory_id: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_memory_completion(self) -> "Oligo":
        if self.sequence_source == "memory_completed" and not self.memory_id:
            raise ValueError("memory_completed oligos must include memory_id")
        return self


class ProtocolOligoSet(BaseModel):
    protocol_id: str
    protocol_name: str
    split: Split
    source_files: list[str] = Field(default_factory=list)
    summary: str | None = None
    major_steps: list[str] = Field(default_factory=list)
    oligos: list[Oligo] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("oligos")
    @classmethod
    def ensure_component_order(cls, oligos: list[Oligo]) -> list[Oligo]:
        for oligo in oligos:
            for index, component in enumerate(oligo.components, start=1):
                if component.order == 0:
                    component.order = index
        return oligos

    @model_validator(mode="after")
    def fill_oligo_protocol_metadata(self) -> "ProtocolOligoSet":
        for oligo in self.oligos:
            if not oligo.protocol_id:
                oligo.protocol_id = self.protocol_id
            if not oligo.protocol_name:
                oligo.protocol_name = self.protocol_name
        return self


class ProtocolNode(BaseModel):
    protocol_id: str
    protocol_name: str
    split: Literal["train", "eval", "test", "memory_global"]
    source_files: list[str] = Field(default_factory=list)


class OligoNode(Oligo):
    allowed_for_memory_completion: bool = True


class ProtocolOligoEdge(BaseModel):
    protocol_id: str
    oligo_id: str
    appeared_as: str
    sequence_source: str
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str | None = None
