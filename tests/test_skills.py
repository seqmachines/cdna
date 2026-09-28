from copy import deepcopy

import pytest

from cdna import MoleculeState, reverse_transcribe, template_switch
from cdna import skills
from cdna.skills import check_reverse_transcribe, check_template_switch


def test_reverse_transcribe_known_product(rna, hybrid):
    result = reverse_transcribe(rna, "RT primer")
    assert isinstance(result, MoleculeState)
    assert result.strands == hybrid.strands
    assert result.id != rna.id
    assert result.origin == "skill"
    assert result.evidence == rna.evidence
    assert result.skill_call_id is None
    assert result.review_status == "unreviewed"
    assert result.stale_since_revision is None
    check_reverse_transcribe(rna, "RT primer", result)


def test_template_switch_known_product(hybrid):
    result = template_switch(hybrid, "TSO handle")
    assert isinstance(result, MoleculeState)
    assert result.strands.top == hybrid.strands.top
    assert result.strands.bottom[0].model_dump() == {
        "name": "TSO handle", "type": "tso", "origin": "skill",
    }
    assert result.strands.bottom[1:] == hybrid.strands.bottom
    assert result.id != hybrid.id
    assert result.origin == "skill"
    assert result.evidence == hybrid.evidence
    assert result.skill_call_id is None
    assert result.review_status == "unreviewed"
    assert result.stale_since_revision is None
    check_template_switch(hybrid, "TSO handle", result)


@pytest.mark.parametrize("bad_strands", [
    {"top": [], "bottom": []},
    {"top": [{"name": "tail", "type": "polyA", "origin": "source"}], "bottom": []},
])
def test_reverse_transcribe_rejects_invalid_substrate(rna, bad_strands):
    data = rna.model_dump()
    data["strands"] = bad_strands
    with pytest.raises(ValueError):
        reverse_transcribe(data, "RT primer")


def test_reverse_transcribe_rejects_hybrid(hybrid):
    with pytest.raises(ValueError, match="single-stranded"):
        reverse_transcribe(hybrid, "RT primer")


@pytest.mark.parametrize("missing", ["top", "bottom"])
def test_template_switch_rejects_single_strand(hybrid, missing):
    getattr(hybrid.strands, missing).clear()
    with pytest.raises(ValueError, match="two-strand"):
        template_switch(hybrid, "TSO handle")


@pytest.mark.parametrize("skill,fixture,oligo", [
    (reverse_transcribe, "rna", "RT primer"),
    (template_switch, "hybrid", "TSO handle"),
])
@pytest.mark.parametrize("as_dict", [False, True])
def test_skills_are_deterministic_and_do_not_share_mutable_data(
    request, skill, fixture, oligo, as_dict,
):
    substrate = request.getfixturevalue(fixture)
    if as_dict:
        substrate = substrate.model_dump()
    before = deepcopy(substrate)
    result = skill(substrate, oligo)
    assert result == skill(substrate, oligo)
    assert substrate == before
    assert type(result) is type(substrate)
    changed = skill(substrate, "another oligo")
    if as_dict:
        assert result["id"] != changed["id"]
        result["strands"]["top"][0]["name"] = "changed"
        result["strands"]["bottom"][1]["name"] = "changed"
        result["evidence"].append("changed")
    else:
        assert result.id != changed.id
        result.strands.top[0].name = "changed"
        result.strands.bottom[1].name = "changed"
        result.evidence.append("changed")
    assert substrate == before


@pytest.mark.parametrize("skill,fixture", [
    (reverse_transcribe, "rna"), (template_switch, "hybrid"),
])
@pytest.mark.parametrize("oligo", ["", "ACGTACGTACGT", "acguacguacgu", 42])
def test_skills_validate_oligos_at_python_boundary(request, skill, fixture, oligo):
    with pytest.raises(ValueError):
        skill(request.getfixturevalue(fixture), oligo)


@pytest.mark.parametrize("skill,fixture", [
    (reverse_transcribe, "rna"), (template_switch, "hybrid"),
])
def test_skills_revalidate_mutated_models(request, skill, fixture):
    substrate = request.getfixturevalue(fixture)
    substrate.strands.top[0].name = "ACGTACGTACGT"
    with pytest.raises(ValueError, match="nucleotide"):
        skill(substrate, "oligo")


@pytest.mark.parametrize("skill,checker,fixture,oligo", [
    (reverse_transcribe, check_reverse_transcribe, "rna", "RT primer"),
    (template_switch, check_template_switch, "hybrid", "TSO handle"),
])
@pytest.mark.parametrize("corruption", [
    "reverse_bottom", "drop_segment", "wrong_name", "wrong_origin",
    "change_top", "change_evidence", "committed", "reviewed", "stale", "reuse_id",
])
def test_postconditions_reject_corrupted_products(
    request, skill, checker, fixture, oligo, corruption,
):
    substrate = request.getfixturevalue(fixture)
    product = skill(substrate, oligo).model_dump()
    bottom = product["strands"]["bottom"]
    if corruption == "reverse_bottom":
        bottom.reverse()
    elif corruption == "drop_segment":
        bottom.pop(1)
    elif corruption == "wrong_name":
        bottom[1]["name"] = "wrong segment"
    elif corruption == "wrong_origin":
        bottom[1]["origin"] = "source"
    elif corruption == "change_top":
        product["strands"]["top"][0]["name"] = "wrong template"
    elif corruption == "change_evidence":
        product["evidence"] = []
    elif corruption == "committed":
        product["skill_call_id"] = "skill-event"
    elif corruption == "reviewed":
        product["review_status"] = "accepted"
    elif corruption == "stale":
        product["stale_since_revision"] = 2
    elif corruption == "reuse_id":
        product["id"] = substrate.id
    with pytest.raises(ValueError):
        checker(substrate, oligo, product)


@pytest.mark.parametrize("skill,fixture", [
    (reverse_transcribe, "rna"), (template_switch, "hybrid"),
])
def test_skills_check_products_before_returning(request, monkeypatch, skill, fixture):
    original = skills._product

    def corrupt_product(*args, **kwargs):
        product = original(*args, **kwargs)
        product["strands"]["bottom"].reverse()
        return product

    monkeypatch.setattr(skills, "_product", corrupt_product)
    with pytest.raises(ValueError):
        skill(request.getfixturevalue(fixture), "oligo")
