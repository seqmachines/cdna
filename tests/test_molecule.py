import pytest
from pydantic import ValidationError

from cdna import MoleculeState


@pytest.mark.parametrize("field", ["id", "label", "segment"])
def test_nucleotide_strings_are_rejected(rna, field):
    data = rna.model_dump()
    if field == "segment":
        data["strands"]["top"][0]["name"] = "ACGTACGTACGT"
    else:
        data[field] = "ACGTACGTACGT"
    with pytest.raises(ValidationError, match="nucleotide"):
        MoleculeState.model_validate(data)


def test_unknown_schema_fields_are_rejected(rna):
    data = rna.model_dump()
    data["sequence"] = "ACGTACGTACGT"
    with pytest.raises(ValidationError, match="Extra inputs"):
        MoleculeState.model_validate(data)


def test_schema_does_not_coerce_types(rna):
    data = rna.model_dump()
    data["stale_since_revision"] = "2"
    with pytest.raises(ValidationError):
        MoleculeState.model_validate(data)
