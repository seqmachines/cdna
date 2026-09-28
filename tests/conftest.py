import pytest

from cdna import MoleculeState


@pytest.fixture
def rna():
    return MoleculeState.model_validate({
        "id": "S_rna", "label": "RNA template",
        "strands": {
            "top": [
                {"name": "leader", "type": "handle", "origin": "source"},
                {"name": "transcript", "type": "insert", "origin": "source"},
                {"name": "poly-A tail", "type": "polyA", "origin": "source"},
            ],
            "bottom": [],
        },
        "origin": "source", "evidence": ["protocol:page-1"],
        "skill_call_id": None, "review_status": "accepted",
        "stale_since_revision": None,
    })


@pytest.fixture
def hybrid(rna):
    data = rna.model_dump()
    data.update(id="S_hybrid", label="RNA/cDNA hybrid", origin="skill")
    data["strands"]["bottom"] = [
        {"name": "leader", "type": "handle", "origin": "skill"},
        {"name": "transcript", "type": "insert", "origin": "skill"},
        {"name": "RT primer", "type": "primer", "origin": "skill"},
    ]
    return MoleculeState.model_validate(data)
