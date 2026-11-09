# Copyright (c) 2026 IndyKite
"""The shipped fleet: valid, in the dataset's vocabulary, and behaving as the gallery promises."""

import json
import re
from pathlib import Path

import pytest
from app import ibac
from app.fleet import REASON_IBAC_SKIPPED, UNMAPPED_DENY, engine_params, load_fleet, params_for

FLEET = Path(__file__).resolve().parent.parent / "data" / "fleet.json"
# The dataset the fleet speaks the vocabulary of, when the repository checkout has it.
MANIFEST = Path(__file__).resolve().parents[4] / "instant-stack" / "data" / "intent-ibac" / "manifest.json"


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
                        or re.match(r"^\$request\.arguments\.\w+$", resource.id)
                    )


def run(fleet, persona_type, persona_id, *evaluations, unmapped="allow"):
    """Run the preview as /api/prompt does, under the given setting for a card without mappings."""
    assert persona_type == "Person"
    params = engine_params(persona_id, {"sub": persona_id, "aud": "intent_agent"}, list(evaluations))
    options = ibac.AuditOptions(subject=persona_id, actors_chain=("console",))
    return fleet.run(intent(*evaluations), params, options, unmapped=unmapped)


def test_a_quote_prompt_routes_to_the_trading_desk_and_generates_a_concrete_request():
    """A quote prompt routes to the trading desk and generates a concrete request."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "Person", "roy", evaluation(("Person", "roy"), "CAN_RETRIEVE", ("Quote", "stock_quote")))
    by_agent = {d["agent"]: d for d in result["discovery"]}
    assert by_agent["trading-desk"]["decision"] is True
    assert by_agent["trading-desk"]["matched_skills"] == ["quote-lookup"]
    assert by_agent["weather"]["skipped"] is True
    assert by_agent["weather"]["reason_code"] == REASON_IBAC_SKIPPED
    assert by_agent["weather"]["record"] is None
    assert all(
        d["reason_code"] == ibac.REASON_INTENT_MISMATCH
        for a, d in by_agent.items()
        if a not in ("trading-desk", "weather")
    )
    assert result["summary"]["matched_agents"] == ["trading-desk"]
    assert result["summary"]["skipped_agents"] == ["weather"]
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert usage["requests"][0]["subject"] == {"type": "Person", "id": "roy"}
    assert usage["requests"][0]["resource"] == {"type": "Quote", "id": "stock_quote"}
    assert usage["requests"][0]["action"] == {"name": "CAN_RETRIEVE"}
    # Every engine call left a record: one discovery per card with mappings (the unmapped one was not
    # asked), one usage per matched skill, in call order.
    actions = [r["action"] for r in result["records"]]
    assert actions.count("IBAC_DISCOVERY") == len(fleet.agents) - 1
    assert actions.count("IBAC_USAGE") == 1
    agents = [d["agent"] for d in result["discovery"] if not d["skipped"]]
    assert actions.index("IBAC_USAGE") == agents.index("trading-desk") + 1


def test_under_deny_a_card_without_mappings_is_asked_and_refused():
    """The deny setting hands the unmapped card to the engine, which answers missing_coaz_mapping with a record."""
    fleet = load_fleet(FLEET)
    quote = evaluation(("Person", "roy"), "CAN_RETRIEVE", ("Quote", "stock_quote"))
    result = run(fleet, "Person", "roy", quote, unmapped=UNMAPPED_DENY)
    weather = next(d for d in result["discovery"] if d["agent"] == "weather")
    assert weather["decision"] is False
    assert weather["skipped"] is False
    assert weather["reason_code"] == ibac.REASON_MISSING_MAPPING
    assert weather["record"]["action"] == "IBAC_DISCOVERY"
    assert result["unmapped"] == UNMAPPED_DENY
    assert result["summary"]["skipped_agents"] == []
    assert sum(1 for r in result["records"] if r["action"] == "IBAC_DISCOVERY") == len(fleet.agents)


def test_a_parameter_reference_resolves_from_the_intent():
    """A parameter reference resolves from the intent."""
    fleet = load_fleet(FLEET)
    result = run(
        fleet,
        "Person",
        "bob",
        evaluation(("Person", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_1"}),
    )
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage["agent"] == "billing"
    assert usage["skill"] == "my-accounts"
    assert usage["requests"] == [
        {
            "subject": {"type": "Person", "id": "bob"},
            "resource": {"type": "AccountTier", "id": "tier_1"},
            "action": {"name": "HAS"},
            "context": {"input_params": params_for(result["params"], 0)},
        },
    ]


def test_a_search_shaped_intent_matches_as_a_search():
    """A search shaped intent matches as a search."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "Person", "leslie", evaluation(("Person", "leslie"), "CAN_VIEW", ("Invoice",)))
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage["skill"] == "customer-360"
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_SEARCH
    assert usage["requests"] == []


def test_an_unmapped_action_matches_nobody():
    """An unmapped action matches nobody."""
    fleet = load_fleet(FLEET)
    result = run(fleet, "Person", "flo", evaluation(("Person", "flo"), "AFFECTED", ("Account",)))
    assert result["summary"]["matched_agents"] == []
    assert result["usage"] == []


def test_engine_params_scope_the_parameters_to_their_evaluation_without_the_markers():
    """Each evaluation keeps its own parameters; the agent's markers and unknown token claims are dropped."""
    params = engine_params(
        "rebecca",
        {"sub": "rebecca", "act": {"sub": "console"}, "nonce": "never-forwarded"},
        [
            {"context": {"parameters": {"subject_source": "caller", "tier": "tier_3"}}},
            {"context": {"parameters": {"tier": "tier_1", "attribute": "price"}}},
        ],
    )
    assert params["token"] == {"sub": "rebecca", "act": {"sub": "console"}}
    assert "subject" not in params  # the engine gets request and token only, nothing from the intent's subject
    assert [e["parameters"] for e in params["evaluations"]] == [
        {"tier": "tier_3"},
        {"tier": "tier_1", "attribute": "price"},
    ]
    arguments = {"tier": "tier_1", "attribute": "price"}
    assert params_for(params, 1) == {"request": {"arguments": arguments}, "token": params["token"]}
    assert params_for(params, 7)["request"]["arguments"] == {}


def test_two_evaluations_with_the_same_parameter_name_generate_two_distinct_requests():
    """'tier_1 for me, tier_3 for ted' must not become tier_1 twice: each request carries its own tier."""
    fleet = load_fleet(FLEET)
    result = run(
        fleet,
        "Person",
        "bob",
        evaluation(("Person", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_1"}),
        evaluation(("Person", "bob"), "HAS", ("AccountTier",), parameters={"tier": "tier_3"}),
    )
    assert len(result["usage"]) == 1
    usage = result["usage"][0]
    assert usage["reason_code"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert [r["resource"]["id"] for r in usage["requests"]] == ["tier_1", "tier_3"]
    assert [r["context"]["input_params"]["request"]["arguments"] for r in usage["requests"]] == [
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
                            "skills": [
                                {"id": "s", "name": "S", "x-coaz-mappings": [{"subject": [{"type": "Person"}]}]},
                            ],
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
    """An intent over the cap is invalid for the engine; splitting per evaluation must not slip past that."""
    fleet = load_fleet(FLEET)
    too_many = [evaluation(("Person", "roy"), "CAN_RETRIEVE", ("Quote", "stock_quote"))] * (ibac.MAX_EVALUATIONS + 1)
    result = run(fleet, "Person", "roy", *too_many)
    assert result["usage"] == []
    assert all(d["reason_code"] == ibac.REASON_INVALID_INTENT for d in result["discovery"] if not d["skipped"])
    params = engine_params("roy", {"sub": "roy"}, too_many)
    usage = fleet.usage("trading-desk", "quote-lookup", intent(*too_many), params, ibac.AuditOptions())
    assert usage["decision"] is False
    assert usage["reason_code"] == ibac.REASON_INVALID_INTENT
    assert usage["requests"] == []
