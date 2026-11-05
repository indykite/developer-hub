# Copyright (c) 2026 IndyKite
"""The shipped fleet: valid, in the dataset's vocabulary, and behaving as the gallery promises."""

import json
import re
from pathlib import Path

import pytest
from app import ibac
from app.fleet import engine_params, load_fleet, params_for

FLEET = Path(__file__).resolve().parent.parent / "data" / "fleet.json"
# The dataset the fleet speaks the vocabulary of, when the repository checkout has it.
MANIFEST = Path(__file__).resolve().parents[4] / "instant-stack" / "data" / "intent" / "manifest.json"


def intent(*evaluations, prompt="p"):
    """Intent."""
    return ibac.Intent.from_dict({"prompt": prompt, "evaluations": list(evaluations)})


def evaluation(subject, action, resource, parameters=None):
    """`subject` is (type, id); `resource` is (type, id) or (type,) for a search."""
    ev = {
        "subject": {"type": subject[0], "id": subject[1]},
        "action": {"name": action},
        "resource": {"type": resource[0], **({"id": resource[1]} if len(resource) > 1 else {})},
    }
    if parameters:
        ev["context"] = {"parameters": parameters}
    return ev


def test_shipped_fleet_loads_with_unique_ids_and_readable_mappings():
    """Shipped fleet loads with unique ids and readable mappings."""
    fleet = load_fleet(FLEET)
    ids = [a.id for a in fleet.agents]
    assert len(ids) == len(set(ids))
    assert fleet.capabilities["weather"].has_mappings() is False
    assert all(fleet.capabilities[a].has_mappings() for a in ids if a != "weather")
    cards = fleet.cards()
    assert [c["id"] for c in cards] == ids
    assert all(c["capability"]["skills"] for c in cards)


@pytest.mark.skipif(not MANIFEST.exists(), reason="instant-stack dataset not in this checkout")
def test_every_mapping_is_a_granted_triple_of_the_intent_dataset():
    """A mapping outside the policies could never be decided: the Intent Agent would not emit it."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    granted = {
        (p["policy"]["subject"]["type"], action, p["policy"]["resource"]["type"])
        for p in manifest["kbac"]
        for action in p["policy"]["actions"]
    }
    fleet = load_fleet(FLEET)
    for agent_id, capability in fleet.capabilities.items():
        for skill in capability.skills:
            for mapping in skill.mappings:
                assert mapping.actions, f"{agent_id}/{skill.id}: a mapping without actions would match any action"
                for subject in mapping.subject:
                    for resource in mapping.resource:
                        for action in mapping.actions:
                            assert (subject.type, action.name, resource.type) in granted, (
                                f"{agent_id}/{skill.id}: {subject.type} {action.name} {resource.type} is not a policy"
                            )


def test_subjects_reference_the_token_and_resources_are_literal_empty_or_parameters():
    """Subjects reference the token and resources are literal empty or parameters."""
    fleet = load_fleet(FLEET)
    for capability in fleet.capabilities.values():
        for skill in capability.skills:
            for mapping in skill.mappings:
                assert all(s.id == "$token.sub" for s in mapping.subject)
                for resource in mapping.resource:
                    assert (
                        resource.id == ""
                        or not resource.id.startswith("$")
                        or re.match(r"^\$parameters\.\w+$", resource.id)
                    )


def run(fleet, persona_type, persona_id, *evaluations):
    """Run."""
    params = engine_params(persona_type, persona_id, {"sub": persona_id, "aud": "intent_agent"}, list(evaluations))
    return fleet.run(intent(*evaluations), params, ibac.AuditOptions(subject=persona_id, actors_chain=("console",)))


def test_a_quote_prompt_routes_to_the_trading_desk_and_generates_a_concrete_request():
    """A quote prompt routes to the trading desk and generates a concrete request."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "User", "roy", evaluation(("User", "roy"), "CAN_RETRIEVE", ("Quote", "stock_quote")))
    by_agent = {d["agent"]: d for d in result["discovery"]}
    assert by_agent["trading-desk"]["decision"] is True
    assert by_agent["trading-desk"]["matched_skills"] == ["quote-lookup"]
    assert by_agent["weather"]["reason_code"] == ibac.REASON_MISSING_MAPPING
    assert all(
        d["reason_code"] == ibac.REASON_INTENT_MISMATCH
        for a, d in by_agent.items()
        if a not in ("trading-desk", "weather")
    )
    assert result["summary"]["matched_agents"] == ["trading-desk"]
    [usage] = result["usage"]
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert usage["requests"][0]["subject"] == {"type": "User", "id": "roy"}
    assert usage["requests"][0]["resource"] == {"type": "Quote", "id": "stock_quote"}
    assert usage["requests"][0]["action"] == {"name": "CAN_RETRIEVE"}
    # Every engine call left a record: one discovery per agent, one usage per matched skill, in call order.
    actions = [r["action"] for r in result["records"]]
    assert actions.count("IBAC_DISCOVERY") == len(fleet.agents)
    assert actions.count("IBAC_USAGE") == 1
    agents = [d["agent"] for d in result["discovery"]]
    assert actions.index("IBAC_USAGE") == agents.index("trading-desk") + 1


def test_a_parameter_reference_resolves_from_the_intent():
    """A parameter reference resolves from the intent."""
    fleet = load_fleet(FLEET)
    result = run(
        fleet,
        "Customer",
        "bob",
        evaluation(("Customer", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_1"}),
    )
    [usage] = result["usage"]
    assert usage["agent"] == "billing"
    assert usage["skill"] == "my-accounts"
    assert usage["requests"] == [
        {
            "subject": {"type": "Customer", "id": "bob"},
            "resource": {"type": "AccountTier", "id": "tier_1"},
            "action": {"name": "HAS"},
            "context": {"input_params": params_for(result["params"], 0)},
        },
    ]


def test_a_search_shaped_intent_matches_as_a_search():
    """A search shaped intent matches as a search."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "User", "leslie", evaluation(("User", "leslie"), "CAN_VIEW", ("Invoice",)))
    [usage] = result["usage"]
    assert usage["skill"] == "customer-360"
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_SEARCH
    assert usage["requests"] == []


def test_an_unmapped_action_matches_nobody():
    """An unmapped action matches nobody."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "User", "flo", evaluation(("User", "flo"), "AFFECTED", ("Account",)))
    assert result["summary"]["matched_agents"] == []
    assert result["usage"] == []


def test_engine_params_scope_the_parameters_to_their_evaluation_without_the_markers():
    """Each evaluation keeps its own parameters; the agent's markers and unknown token claims are dropped."""
    params = engine_params(
        "Customer",
        "rebecca",
        {"sub": "rebecca", "act": {"sub": "console"}, "nonce": "never-forwarded"},
        [
            {"context": {"parameters": {"subject_source": "caller", "tier": "tier_3"}}},
            {"context": {"parameters": {"tier": "tier_1", "attribute": "price"}}},
        ],
    )
    assert params["token"] == {"sub": "rebecca", "act": {"sub": "console"}}
    assert params["subject"] == {"type": "Customer", "id": "rebecca"}
    assert [e["parameters"] for e in params["evaluations"]] == [
        {"tier": "tier_3"},
        {"tier": "tier_1", "attribute": "price"},
    ]
    assert params_for(params, 1)["parameters"] == {"tier": "tier_1", "attribute": "price"}
    assert params_for(params, 7)["parameters"] == {}


def test_two_evaluations_with_the_same_parameter_name_generate_two_distinct_requests():
    """'tier_1 for me, tier_3 for ted' must not become tier_1 twice: each request carries its own tier."""
    fleet = load_fleet(FLEET)
    result = run(
        fleet,
        "Customer",
        "bob",
        evaluation(("Customer", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_1"}),
        evaluation(("Customer", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_3"}),
    )
    [usage] = result["usage"]
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert [r["resource"]["id"] for r in usage["requests"]] == ["tier_1", "tier_3"]
    assert [r["context"]["input_params"]["parameters"] for r in usage["requests"]] == [
        {"tier": "tier_1"},
        {"tier": "tier_3"},
    ]
    # One record for the skill, however many evaluations it was confirmed against.
    assert sum(1 for r in result["records"] if r["action"] == "IBAC_USAGE") == 1


def test_a_fleet_with_a_bad_mapping_is_refused(tmp_path):
    """A fleet with a bad mapping is refused."""
    bad = tmp_path / "fleet.json"
    bad.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "x",
                        "card": {
                            "name": "X",
                            "skills": [{"id": "s", "name": "S", "x-coaz-mappings": [{"subject": [{"type": "User"}]}]}],
                        },
                    },
                ],
            },
        ),
    )
    with pytest.raises(ValueError, match="resource"):
        load_fleet(bad)


def test_engine_params_ignore_a_malformed_context():
    """The what-if endpoint hands the engine a client-supplied intent: odd shapes are skipped, never raised on."""
    params = engine_params(
        "User",
        "roy",
        {"sub": "roy"},
        [
            {"context": "x"},
            {"context": {"parameters": ["not", "a", "map"]}},
            {"context": None},
            "not an evaluation",
            {"context": {"parameters": {"tier": "tier_1"}}},
        ],
    )
    assert [e["parameters"] for e in params["evaluations"]] == [{}, {}, {}, {}, {"tier": "tier_1"}]


def test_an_oversized_intent_is_refused_whole_at_usage():
    """Sixty-five evaluations are invalid for the engine; splitting per evaluation must not slip past that."""
    fleet = load_fleet(FLEET)
    too_many = [evaluation(("User", "roy"), "CAN_RETRIEVE", ("Quote", "stock_quote"))] * (ibac.MAX_EVALUATIONS + 1)
    result = run(fleet, "User", "roy", *too_many)
    assert result["usage"] == []
    assert all(d["reason_code"] == ibac.REASON_INVALID_INTENT for d in result["discovery"])
    params = engine_params("User", "roy", {"sub": "roy"}, too_many)
    usage = fleet.usage("trading-desk", "quote-lookup", intent(*too_many), params, ibac.AuditOptions())
    assert usage["decision"] is False
    assert usage["reason_code"] == ibac.REASON_INVALID_INTENT
    assert usage["requests"] == []
