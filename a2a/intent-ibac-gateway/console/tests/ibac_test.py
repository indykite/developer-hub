# Copyright (c) 2026 IndyKite
"""The console's mapping check, exercised with the demo's own cards and vocabulary."""

import re

import pytest
from app import ibac
from app.ibac import (
    Action,
    Capability,
    CoazMapping,
    Entity,
    Evaluation,
    EvaluationContext,
    Intent,
    Skill,
    evaluate_discovery,
    evaluate_usage,
    read_mappings,
)

# --- builders in the demo's words -----------------------------------------------


def entity(ref):
    """An entity from "Type" or "Type/id"."""
    kind, _, ident = ref.partition("/")
    return Entity(type=kind, id=ident)


def ev(subject, action, resource, *, parameters=None):
    """One evaluation of an intent; subject and resource are "Type" or "Type/id"."""
    return Evaluation(
        subject=entity(subject),
        resource=entity(resource),
        action=Action(name=action) if action is not None else None,
        context=EvaluationContext(parameters=parameters or {}),
    )


def mapping(subject_type, resource_type, *actions, subject_id="$token.sub", resource_id=""):
    """One coarse-authorization mapping."""
    return CoazMapping(
        subject=[Entity(type=subject_type, id=subject_id)],
        resource=[Entity(type=resource_type, id=resource_id)],
        actions=[Action(name=a) for a in actions],
    )


def skill(skill_id, *mappings):
    """One skill of a card."""
    return Skill(id=skill_id, name=skill_id.replace("-", " ").title(), mappings=list(mappings))


QUOTE_LOOKUP = skill("quote-lookup", mapping("User", "Quote", "CAN_RETRIEVE", "GET_PRICE", resource_id="stock_quote"))
CORE_BANKING = skill("core-banking", mapping("User", "Customer", "CAN_LIST_CUSTOMER", "CAN_ACCESS_CUSTOMER"))
MY_ACCOUNTS = skill(
    "my-accounts",
    mapping("Customer", "Account", "OWNS", "VIEW_BALANCE"),
    mapping("Customer", "AccountTier", "HAS", resource_id="$request.arguments.tier"),
    mapping("Customer", "AccountTier", "HAS_TIER"),
)
TRADING = Capability(name="Trading Desk Agent", skills=[QUOTE_LOOKUP, CORE_BANKING])
BILLING = Capability(
    name="Billing Agent",
    skills=[skill("my-invoices", mapping("Customer", "Invoice", "VIEW_INVOICE")), MY_ACCOUNTS],
)
WEATHER = Capability(name="Weather Agent", skills=[skill("forecast")])

ROY_QUOTE = ev("User/roy", "GET_PRICE", "Quote/stock_quote")
BOB_TIER = ev("Customer/bob", "HAS", "AccountTier", parameters={"tier": "tier_1"})
PARAMS = {"request": {"arguments": {}}, "token": {"sub": "roy"}}


def intent(*evaluations):
    """An intent of the given evaluations."""
    return Intent(prompt="p", evaluations=list(evaluations))


def matched(response):
    """The skills matched per evaluation."""
    return [e.matched_skills for e in response.context.evaluations]


def requests_of(response):
    """The generated requests as JSON."""
    return [r.as_dict() for r in response.context.generated_request.authzen_requests]


# --- discovery --------------------------------------------------------------------


def test_discovery_matches_a_skill_on_the_three_types_and_ignores_ids():
    """Roy's quote question matches quote-lookup; ids play no part at this stage."""
    resp = evaluate_discovery(intent(ROY_QUOTE), TRADING)
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH
    assert matched(resp) == [["quote-lookup"]]
    other_id = ev("User/leslie", "GET_PRICE", "Quote/another_quote")
    assert evaluate_discovery(intent(other_id), TRADING).decision is True


def test_discovery_lists_the_skills_per_evaluation_in_card_order():
    """Each evaluation names the skills that declare it, in the card's order."""
    customers = ev("User/roy", "CAN_LIST_CUSTOMER", "Customer")
    both = Capability(
        name="x",
        skills=[
            skill("a", mapping("User", "Quote", "GET_PRICE")),
            skill("b", mapping("User", "Quote", "GET_PRICE")),
            CORE_BANKING,
        ],
    )
    resp = evaluate_discovery(intent(ROY_QUOTE, customers), both)
    assert matched(resp) == [["a", "b"], ["core-banking"]]


def test_discovery_mismatch_names_every_evaluation_with_no_skill():
    """A customer asking about an invoice does not match the Trading Desk."""
    invoice = ev("Customer/bob", "VIEW_INVOICE", "Invoice")
    resp = evaluate_discovery(intent(invoice), TRADING)
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH
    assert resp.context.reason == ibac.REASON_TEXT_INTENT_MISMATCH
    assert matched(resp) == [[]]


@pytest.mark.parametrize(
    "evaluation",
    [
        ev("Customer/bob", "GET_PRICE", "Quote/stock_quote"),
        ev("User/roy", "GET_PRICE", "Invoice"),
        ev("User/roy", "CAN_DISCLOSE", "Quote/stock_quote"),
    ],
    ids=["subject type", "resource type", "action"],
)
def test_discovery_needs_all_three_to_agree(evaluation):
    """A different subject type, resource type or action is a mismatch."""
    assert evaluate_discovery(intent(evaluation), TRADING).decision is False


def test_discovery_without_mappings_or_without_evaluations_never_matches():
    """The Weather card has no mappings; an empty intent has nothing to match."""
    resp = evaluate_discovery(intent(ROY_QUOTE), WEATHER)
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_MISSING_MAPPING
    assert resp.context.reason == ibac.REASON_TEXT_MISSING_MAPPING
    assert evaluate_discovery(intent(), TRADING).context.reason_code == ibac.REASON_INTENT_MISMATCH


def test_an_action_search_matches_only_a_mapping_that_allows_any_action():
    """An evaluation without an action matches an unrestricted mapping only."""
    any_action = Capability(name="x", skills=[skill("open", mapping("User", "Quote"))])
    no_action = ev("User/roy", None, "Quote/stock_quote")
    assert evaluate_discovery(intent(no_action), any_action).decision is True
    assert evaluate_discovery(intent(no_action), TRADING).decision is False


# --- usage ------------------------------------------------------------------------


def test_usage_resolves_the_caller_and_a_literal_resource_into_one_request():
    """quote-lookup binds $token.sub to roy and names stock_quote: one request comes out."""
    resp = evaluate_usage(intent(ROY_QUOTE), TRADING.with_skill("quote-lookup"), PARAMS)
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert resp.context.generated_request.skill_id == "quote-lookup"
    assert requests_of(resp) == [
        {
            "subject": {"type": "User", "id": "roy"},
            "resource": {"type": "Quote", "id": "stock_quote"},
            "action": {"name": "GET_PRICE"},
            "context": {"input_params": PARAMS},
        },
    ]


def test_usage_resolves_a_parameter_reference_with_a_dotted_path():
    """my-accounts binds the tier from the intent's parameters."""
    params = {"request": {"arguments": {"tier": "tier_1"}}, "token": {"sub": "bob"}}
    resp = evaluate_usage(intent(BOB_TIER), BILLING.with_skill("my-accounts"), params)
    assert resp.decision is True
    [req] = requests_of(resp)
    assert req["subject"] == {"type": "Customer", "id": "bob"}
    assert req["resource"] == {"type": "AccountTier", "id": "tier_1"}


def test_an_empty_resource_id_is_a_search_match_with_no_request():
    """core-banking leaves the customer id empty: the skill matches, nothing is generated."""
    customers = ev("User/roy", "CAN_LIST_CUSTOMER", "Customer")
    resp = evaluate_usage(intent(customers), TRADING.with_skill("core-banking"), PARAMS)
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH
    assert resp.context.generated_request is None


@pytest.mark.parametrize(
    "params",
    [None, {"token": {}}, {"token": {"sub": ""}}, {"token": {"sub": 42}}, {"token": "flat"}],  # nosec B105
    ids=["no params", "no sub", "empty sub", "not a string", "not a map"],
)
def test_an_unresolved_reference_drops_the_entity_and_the_mapping_does_not_match(params):
    """Without a resolvable $token.sub there is no subject to build a request for."""
    resp = evaluate_usage(intent(ROY_QUOTE), TRADING.with_skill("quote-lookup"), params)
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH
    assert resp.context.generated_request is None


def test_a_missing_intent_parameter_is_a_mismatch_but_a_sibling_mapping_can_still_match():
    """Without request.arguments.tier HAS cannot resolve; HAS_TIER on the same skill still matches as a search."""
    no_tier = ev("Customer/bob", "HAS", "AccountTier")
    bob = {"token": {"sub": "bob"}}
    assert evaluate_usage(intent(no_tier), BILLING.with_skill("my-accounts"), bob).decision is False
    tiers = ev("Customer/bob", "HAS_TIER", "AccountTier")
    resp = evaluate_usage(intent(tiers), BILLING.with_skill("my-accounts"), bob)
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH


def test_usage_builds_one_request_per_subject_and_resource_pair():
    """Two literal subjects and two literal resources make four requests."""
    pairs = CoazMapping(
        subject=[Entity("User", "roy"), Entity("User", "millicent")],
        resource=[Entity("Quote", "stock_quote"), Entity("Quote", "bond_quote")],
        actions=[Action("GET_PRICE")],
    )
    resp = evaluate_usage(intent(ROY_QUOTE), Capability(name="x", skills=[skill("s", pairs)]))
    ids = sorted((r["subject"]["id"], r["resource"]["id"]) for r in requests_of(resp))
    assert ids == [
        ("millicent", "bond_quote"),
        ("millicent", "stock_quote"),
        ("roy", "bond_quote"),
        ("roy", "stock_quote"),
    ]


def test_usage_collects_requests_from_every_matching_evaluation_and_needs_one_match():
    """Of two evaluations only the quote one matches quote-lookup; no match at all is negative."""
    invoice = ev("Customer/bob", "VIEW_INVOICE", "Invoice")
    resp = evaluate_usage(intent(invoice, ROY_QUOTE), TRADING.with_skill("quote-lookup"), PARAMS)
    assert resp.decision is True
    assert len(requests_of(resp)) == 1
    assert evaluate_usage(intent(invoice), TRADING.with_skill("quote-lookup"), PARAMS).decision is False


def test_usage_wants_exactly_one_skill_and_valid_input():
    """A whole card, a card without mappings, an invalid intent or an invalid card each get their own reason code."""
    one = TRADING.with_skill("quote-lookup")
    assert evaluate_usage(intent(ROY_QUOTE), TRADING, PARAMS).context.reason_code == ibac.REASON_INCORRECT_NUM_SKILLS
    assert evaluate_usage(intent(ROY_QUOTE), WEATHER, PARAMS).context.reason_code == ibac.REASON_MISSING_MAPPING
    bad_intent = Intent(evaluations=[Evaluation(action=Action("GET_PRICE"))])
    assert evaluate_usage(bad_intent, one, PARAMS).context.reason_code == ibac.REASON_INVALID_INTENT
    bad_card = Capability(name="x", skills=[Skill(name="no id", mappings=[mapping("User", "Quote")])])
    assert evaluate_usage(intent(ROY_QUOTE), bad_card, PARAMS).context.reason_code == ibac.REASON_INVALID_CAPABILITY
    too_many = Intent(evaluations=[ROY_QUOTE] * (ibac.MAX_EVALUATIONS + 1))
    assert evaluate_usage(too_many, one, PARAMS).context.reason_code == ibac.REASON_INVALID_INTENT


# --- records ----------------------------------------------------------------------


def test_discovery_and_usage_records_carry_the_card_the_skills_and_the_reason():
    """One record per call: discovery with the skills matched per evaluation, usage with the skill and reason code."""
    options = ibac.AuditOptions(
        prompt="What is the price of the quote stock_quote?",
        subject="roy",
        actors_chain=("console",),
    )
    discovery = ibac.discovery_audit_record(evaluate_discovery(intent(ROY_QUOTE), TRADING), TRADING, options)
    assert discovery["action"] == "IBAC_DISCOVERY"
    assert discovery["decision"] == "AUTHORIZED"
    assert discovery["capabilityName"] == "Trading Desk Agent"
    assert discovery["skillIDs"] == ["quote-lookup", "core-banking"]
    assert discovery["evaluations"] == [
        {"subjectType": "User", "resourceType": "Quote", "action": "GET_PRICE", "matchedSkills": ["quote-lookup"]},
    ]
    assert discovery["subject"] == "roy"
    assert discovery["actorsChain"] == ["console"]
    one = TRADING.with_skill("quote-lookup")
    usage = ibac.usage_audit_record(evaluate_usage(intent(ROY_QUOTE), one, PARAMS), one, options)
    assert usage["action"] == "IBAC_USAGE"
    assert usage["skillID"] == "quote-lookup"
    assert usage["reasonCode"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    whole = ibac.usage_audit_record(evaluate_usage(intent(ROY_QUOTE), TRADING, PARAMS), TRADING)
    assert whole["decision"] == "NOT_AUTHORIZED"
    assert whole["skillID"] == ""


# --- reading a card ---------------------------------------------------------------

RAW = [
    {
        "subject": [{"type": "User", "id": "$token.sub"}],
        "resource": [{"type": "Quote", "id": "stock_quote"}],
        "action": [{"name": "CAN_RETRIEVE"}, {"name": "GET_PRICE"}],
    },
]


def test_read_mappings_from_an_a2a_skill_and_from_an_mcp_entry():
    """A2A skills carry the list in the entry, MCP tools in their _meta, which wins when both are present."""
    want = [QUOTE_LOOKUP.mappings[0]]
    assert read_mappings({"id": "quote-lookup", "name": "Quote lookup", "x-coaz-mappings": RAW}) == want
    assert read_mappings({"name": "get_quote", "inputSchema": {}, "_meta": {"x-coaz-mappings": RAW}}) == want
    assert read_mappings({"x-coaz-mappings": [], "_meta": {"x-coaz-mappings": RAW}}) == want


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "forecast"},
        {"name": "forecast", "_meta": {"other": 1}},
        {"name": "forecast", "_meta": None},
        {"name": "forecast", "x-coaz-mappings": []},
    ],
)
def test_read_mappings_returns_none_without_an_error(entry):
    """A skill without mappings is a valid skill that will never match."""
    assert read_mappings(entry) == []


@pytest.mark.parametrize(
    ("entry", "want"),
    [
        ("not an object", "capability entry"),
        ({"_meta": "x"}, "_meta"),
        ({"x-coaz-mappings": {"a": 1}}, "not a list"),
        ({"x-coaz-mappings": [None]}, "mapping is null"),
        ({"x-coaz-mappings": [{"subject": [{"type": "x"}], "resource": [{"type": "Quote"}]}]}, "subject[0].type"),
        ({"x-coaz-mappings": [{"resource": [{"type": "Quote"}]}]}, "subject"),
        (
            {"x-coaz-mappings": [{"subject": [{"type": "User"}], "resource": [{"type": "Quote"}], "action": [{}]}]},
            "action[0].name",
        ),
    ],
)
def test_read_mappings_names_the_field_it_cannot_read(entry, want):
    """An unreadable mapping is refused with the field in the message, so the whole card can be refused."""
    with pytest.raises(ibac.ValidationError, match=re.escape(want)):
        read_mappings(entry)


def test_capability_from_card_keeps_the_skills_and_their_mappings():
    """A card with one mapped and one unmapped skill reads into a capability of two skills."""
    card = {
        "name": "Trading Desk Agent",
        "skills": [
            {"id": "quote-lookup", "name": "Quote lookup", "x-coaz-mappings": RAW},
            {"id": "chat", "name": "Chat"},
        ],
    }
    cap = ibac.capability_from_card(card)
    assert [s.id for s in cap.skills] == ["quote-lookup", "chat"]
    assert cap.skills[1].mappings == []
    assert cap.with_skill("chat").skills == [cap.skills[1]]


def test_intent_from_dict_reads_the_agent_artifact():
    """The Intent Agent's artifact reads into an intent; `filters` and an absent action are kept as such."""
    raw = {
        "prompt": "p",
        "evaluations": [
            {
                "subject": {"type": "User", "id": "roy"},
                "action": {"name": "GET_PRICE"},
                "resource": {"type": "Quote", "id": "stock_quote"},
                "context": {
                    "parameters": {"attribute": "price"},
                    "filters": "resource.price > 100.0",
                    "qualifiers": {"time": "pm"},
                },
            },
            {"subject": {"type": "User", "id": "roy"}, "resource": {"type": "Quote", "id": "stock_quote"}},
        ],
    }
    parsed = Intent.from_dict(raw)
    parsed.validate()
    assert parsed.evaluations[0].action_name == "GET_PRICE"
    assert parsed.evaluations[0].context.filter == "resource.price > 100.0"
    assert parsed.evaluations[1].action is None
    assert parsed.evaluations[1].as_dict() == {
        "subject": {"type": "User", "id": "roy"},
        "resource": {"type": "Quote", "id": "stock_quote"},
    }
