# Copyright (c) 2026 IndyKite
"""The engine port, checked against the same cases the gateway's engine is tested with."""

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

# --- builders ----------------------------------------------------------------


def ev(subject_type, resource_type, action, parameters=None):
    return Evaluation(
        subject=Entity(type=subject_type),
        resource=Entity(type=resource_type),
        action=Action(name=action) if action is not None else None,
        context=EvaluationContext(parameters=parameters or {}),
    )


def skill(skill_id, *mappings):
    return Skill(id=skill_id, name=f"Skill {skill_id}", mappings=list(mappings))


def mapping(subject_type, resource_type, *actions):
    return CoazMapping(
        subject=[Entity(type=subject_type)],
        resource=[Entity(type=resource_type)],
        actions=[Action(name=a) for a in actions],
    )


def mapping_with_ids(subject_id, resource_id, context=None):
    return CoazMapping(
        context=context or {},
        subject=[Entity(type="User", id=subject_id)],
        resource=[Entity(type="Document", id=resource_id)],
    )


def capability(*mappings):
    return Capability(name="Docs", skills=[skill("skill-1", *mappings)])


def intent(*evaluations):
    return Intent(prompt="read the document", evaluations=list(evaluations))


def request(subject_id, resource_id, action, input_params=None):
    return {
        "subject": {"type": "User", "id": subject_id},
        "resource": {"type": "Document", "id": resource_id},
        "action": {"name": action},
        **({"context": {"input_params": input_params}} if input_params else {}),
    }


def requests_of(response):
    return [r.as_dict() for r in response.context.generated_request.authzen_requests]


# --- usage -------------------------------------------------------------------


def test_usage_rejects_an_invalid_intent():
    bad = Intent(evaluations=[Evaluation(action=Action(name="read"))])
    resp = evaluate_usage(bad, capability(mapping("User", "Document")))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INVALID_INTENT
    assert resp.context.reason


def test_usage_rejects_an_invalid_capability():
    cap = Capability(skills=[Skill(name="no id", mappings=[mapping("User", "Document")])])
    resp = evaluate_usage(intent(ev("User", "Document", "read")), cap)
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INVALID_CAPABILITY


def test_usage_needs_exactly_one_skill():
    resp = evaluate_usage(intent(ev("User", "Document", "read")), Capability())
    assert resp.context.reason_code == ibac.REASON_MISSING_MAPPING
    two = Capability(
        skills=[skill("skill-1", mapping("User", "Document")), skill("skill-2", mapping("User", "Document"))],
    )
    resp = evaluate_usage(intent(ev("User", "Document", "read")), two)
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INCORRECT_NUM_SKILLS
    assert resp.context.reason == ibac.REASON_TEXT_INCORRECT_NUM_SKILLS


def test_usage_without_mappings_is_a_missing_mapping():
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability())
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_MISSING_MAPPING
    assert resp.context.reason == ibac.REASON_TEXT_MISSING_MAPPING


@pytest.mark.parametrize(
    ("evaluation", "coaz"),
    [
        (ev("User", "Document", "read"), mapping("Service", "Document")),
        (ev("User", "Document", "read"), mapping("User", "Folder")),
        (ev("User", "Document", "read"), mapping("User", "Document", "write")),
    ],
    ids=["subject type differs", "resource type differs", "action not allowed"],
)
def test_usage_mismatch(evaluation, coaz):
    resp = evaluate_usage(intent(evaluation), capability(coaz))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH
    assert resp.context.generated_request is None


def test_usage_of_an_empty_intent_is_a_mismatch():
    resp = evaluate_usage(Intent(), capability(mapping("User", "Document")))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH


def test_usage_builds_the_request_from_literal_ids():
    resp = evaluate_usage(
        intent(ev("User", "Document", "read", {"foo": "bar"})),
        capability(mapping_with_ids("user-1", "doc-1")),
    )
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert resp.context.generated_request.skill_id == "skill-1"
    assert requests_of(resp) == [request("user-1", "doc-1", "read")]


def test_usage_matches_any_action_when_the_mapping_names_none_and_one_of_several_otherwise():
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability(mapping_with_ids("user-1", "doc-1")))
    assert len(requests_of(resp)) == 1
    coaz = mapping_with_ids("user-1", "doc-1")
    coaz.actions = [Action(name="write"), Action(name="read")]
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability(coaz))
    assert resp.decision is True
    assert len(requests_of(resp)) == 1


def test_usage_matches_when_any_one_of_several_mappings_matches():
    resp = evaluate_usage(
        intent(ev("User", "Document", "read")),
        capability(mapping("Service", "Document"), mapping_with_ids("user-1", "doc-1")),
    )
    assert resp.decision is True
    assert requests_of(resp) == [request("user-1", "doc-1", "read")]


def test_usage_tolerates_an_evaluation_without_a_context():
    evaluation = ev("User", "Document", "read")
    evaluation.context = None
    resp = evaluate_usage(intent(evaluation), capability(mapping_with_ids("user-1", "doc-1")))
    assert requests_of(resp) == [request("user-1", "doc-1", "read")]


def test_usage_resolves_parameter_references():
    params = {"sub": "user-2", "docId": "doc-2"}
    resp = evaluate_usage(
        intent(ev("User", "Document", "read", {"docId": "doc-1"})),
        capability(mapping_with_ids("$sub", "$docId")),
        params,
    )
    assert resp.decision is True
    assert requests_of(resp) == [request("user-2", "doc-2", "read", params)]
    # The mapping's own context does not take part in the resolution; params do.
    resp = evaluate_usage(
        intent(ev("User", "Document", "read", {"docId": "doc-1"})),
        capability(mapping_with_ids("user-1", "$docId", {"docId": "doc-3"})),
        params,
    )
    assert requests_of(resp) == [request("user-1", "doc-2", "read", params)]


def test_usage_resolves_dotted_paths_into_nested_maps():
    params = {"user": {"profile": {"id": "user-1"}}}
    resp = evaluate_usage(
        intent(ev("User", "Document", "read")),
        capability(mapping_with_ids("$user.profile.id", "doc-1")),
        params,
    )
    assert requests_of(resp) == [request("user-1", "doc-1", "read", params)]


def test_usage_builds_one_request_per_subject_and_resource_pair():
    coaz = CoazMapping(
        subject=[Entity("User", "user-1"), Entity("User", "user-2")],
        resource=[Entity("Document", "doc-1"), Entity("Document", "doc-2")],
    )
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability(coaz))
    got = requests_of(resp)
    assert len(got) == 4
    for s in ("user-1", "user-2"):
        for r in ("doc-1", "doc-2"):
            assert request(s, r, "read") in got


@pytest.mark.parametrize(
    ("subject_id", "resource_id", "eval_params", "params"),
    [
        ("$sub", "doc-1", None, None),
        ("user-1", "$docId", None, None),
        ("$sub", "doc-1", None, {"sub": 42}),
        ("$id", "doc-1", None, {"user": "flat"}),
        ("$other.sub", "doc-1", None, {"sub": "user-1"}),
        ("$sub", "doc-1", None, {"sub": ""}),
        ("user-1", "$docId", {"docId": ""}, None),
        ("$user.id", "doc-1", None, {"user": {"id": ""}}),
    ],
    ids=[
        "subject claim missing",
        "resource parameter missing",
        "value is not a string",
        "nested path missing",
        "prefix unknown",
        "claim is an empty string",
        "evaluation parameter is an empty string",
        "nested claim is an empty string",
    ],
)
def test_usage_drops_an_entity_whose_id_cannot_be_resolved(subject_id, resource_id, eval_params, params):
    resp = evaluate_usage(
        intent(ev("User", "Document", "read", eval_params)),
        capability(mapping_with_ids(subject_id, resource_id)),
        params,
    )
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH
    assert resp.context.generated_request is None


def test_usage_keeps_the_resolvable_entities_of_the_same_mapping():
    coaz = CoazMapping(
        subject=[Entity("User", "$sub"), Entity("User", "user-2")],
        resource=[Entity("Document", "doc-1")],
    )
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability(coaz))
    assert resp.decision is True
    assert requests_of(resp) == [request("user-2", "doc-1", "read")]


@pytest.mark.parametrize(("subject_id", "resource_id"), [("", "doc-1"), ("user-1", ""), ("", "")])
def test_usage_with_an_empty_literal_id_is_a_search_match(subject_id, resource_id):
    resp = evaluate_usage(intent(ev("User", "Document", "read")), capability(mapping_with_ids(subject_id, resource_id)))
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH
    assert resp.context.generated_request is None


def test_usage_returns_only_the_requests_with_complete_ids():
    valid = CoazMapping(subject=[Entity("Service", "service-1")], resource=[Entity("Folder", "folder-1")])
    resp = evaluate_usage(
        intent(ev("User", "Document", "read"), ev("Service", "Folder", "read")),
        capability(mapping_with_ids("", "doc-1"), valid),
    )
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    assert [r.subject.id for r in resp.context.generated_request.authzen_requests] == ["service-1"]


def test_usage_collects_the_requests_of_every_matching_evaluation_only():
    resp = evaluate_usage(
        intent(ev("User", "Document", "read"), ev("Service", "Folder", "read")),
        capability(mapping_with_ids("user-1", "doc-1")),
    )
    assert requests_of(resp) == [request("user-1", "doc-1", "read")]
    resp = evaluate_usage(
        intent(ev("User", "Document", "read"), ev("User", "Document", "write")),
        capability(mapping_with_ids("user-1", "doc-1")),
    )
    assert requests_of(resp) == [request("user-1", "doc-1", "read"), request("user-1", "doc-1", "write")]


def test_usage_without_an_action_matches_only_an_unrestricted_mapping_and_as_a_search():
    search = evaluate_usage(intent(ev("User", "Document", None)), capability(mapping_with_ids("user-1", "doc-1")))
    assert search.decision is True
    assert search.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH
    restricted = evaluate_usage(intent(ev("User", "Document", None)), capability(mapping("User", "Document", "read")))
    assert restricted.decision is False


# --- discovery ---------------------------------------------------------------


def matched(response):
    return [e.matched_skills for e in response.context.evaluations]


def test_discovery_without_mappings_is_a_missing_mapping():
    one = Intent(evaluations=[ev("User", "Document", "read")])
    resp = evaluate_discovery(one, Capability(skills=[skill("skill-1")]))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_MISSING_MAPPING
    assert resp.context.reason == ibac.REASON_TEXT_MISSING_MAPPING
    assert evaluate_discovery(one, Capability()).context.reason_code == ibac.REASON_MISSING_MAPPING


def test_discovery_mismatch_lists_every_evaluation_with_no_skills():
    one = Intent(evaluations=[ev("User", "Document", "read")])
    resp = evaluate_discovery(one, Capability(skills=[skill("skill-1", mapping("Service", "Document"))]))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH
    assert resp.context.reason == ibac.REASON_TEXT_INTENT_MISMATCH
    assert resp.context.evaluations[0].evaluation is one.evaluations[0]
    assert matched(resp) == [[]]
    resp = evaluate_discovery(one, Capability(skills=[skill("skill-1", mapping("User", "Document", "write"))]))
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH


def test_discovery_lists_every_evaluation_and_the_skills_that_matched_it():
    two = Intent(evaluations=[ev("User", "Document", "read"), ev("Service", "Report", "delete")])
    cap = Capability(
        skills=[
            skill("skill-1", mapping("User", "Document", "read")),
            skill("skill-2", mapping("Service", "Document", "write")),
        ],
    )
    resp = evaluate_discovery(two, cap)
    assert resp.decision is True
    assert resp.context.reason_code == ibac.REASON_INTENT_MATCH_SEARCH
    assert [e.evaluation for e in resp.context.evaluations] == two.evaluations
    assert matched(resp) == [["skill-1"], []]


def test_discovery_matches_the_same_skill_under_several_evaluations():
    two = Intent(evaluations=[ev("User", "Document", "read"), ev("User", "Document", "write")])
    resp = evaluate_discovery(two, Capability(skills=[skill("skill-1", mapping("User", "Document"))]))
    assert matched(resp) == [["skill-1"], ["skill-1"]]


def test_discovery_collects_skills_per_evaluation_and_in_capability_order():
    two = Intent(evaluations=[ev("User", "Document", "read"), ev("Service", "Document", "write")])
    cap = Capability(
        skills=[
            skill("skill-1", mapping("User", "Document", "read")),
            skill("skill-2", mapping("Service", "Document", "write")),
        ],
    )
    assert matched(evaluate_discovery(two, cap)) == [["skill-1"], ["skill-2"]]
    both = Capability(
        skills=[
            skill("skill-1", mapping("User", "Document", "read")),
            skill("skill-2", mapping("User", "Document", "read")),
        ],
    )
    assert matched(evaluate_discovery(Intent(evaluations=[ev("User", "Document", "read")]), both)) == [
        ["skill-1", "skill-2"],
    ]


def test_discovery_of_an_empty_intent_is_a_mismatch():
    resp = evaluate_discovery(Intent(), Capability(skills=[skill("skill-1", mapping("User", "Document"))]))
    assert resp.decision is False
    assert resp.context.reason_code == ibac.REASON_INTENT_MISMATCH


def test_discovery_ignores_ids():
    evaluation = ev("User", "Document", "read")
    evaluation.subject.id = "user-1"
    coaz = mapping("User", "Document")
    coaz.subject[0].id = "user-2"
    resp = evaluate_discovery(Intent(evaluations=[evaluation]), Capability(skills=[skill("skill-1", coaz)]))
    assert resp.decision is True


# --- audit records -----------------------------------------------------------


def test_discovery_record_names_the_skills_in_order_and_the_matches_per_evaluation():
    cap = Capability(
        name="Docs",
        skills=[skill("b", mapping("User", "Folder", "read")), skill("a", mapping("User", "Document", "read"))],
    )
    one = Intent(prompt="find the document", evaluations=[ev("User", "Document", "read")])
    options = ibac.AuditOptions(prompt=one.prompt, subject="user-1", actors_chain=("agent-1",))
    record = ibac.discovery_audit_record(evaluate_discovery(one, cap), cap, options)
    assert record["decision"] == "AUTHORIZED"
    assert record["action"] == "IBAC_DISCOVERY"
    assert record["capabilityName"] == "Docs"
    assert record["skillIDs"] == ["b", "a"]
    assert record["evaluations"] == [
        {"subjectType": "User", "resourceType": "Document", "action": "read", "matchedSkills": ["a"]},
    ]
    assert record["prompt"] == "find the document"
    assert record["subject"] == "user-1"
    assert record["actorsChain"] == ["agent-1"]
    miss = ibac.discovery_audit_record(
        evaluate_discovery(one, Capability(name="Docs", skills=[skill("a", mapping("User", "Folder", "read"))])),
        cap,
    )
    assert miss["decision"] == "NOT_AUTHORIZED"
    assert miss["evaluations"][0]["matchedSkills"] == []


def test_usage_record_names_the_skill_and_the_reason_code():
    cap = capability(mapping_with_ids("u1", "d1"))
    record = ibac.usage_audit_record(evaluate_usage(intent(ev("User", "Document", "read")), cap), cap)
    assert record["decision"] == "AUTHORIZED"
    assert record["action"] == "IBAC_USAGE"
    assert record["skillID"] == "skill-1"
    assert record["reasonCode"] == ibac.REASON_INTENT_MATCH_AUTHZEN_REQUIRED
    two = Capability(
        name="Docs",
        skills=[skill("a", mapping("User", "Document")), skill("b", mapping("User", "Document"))],
    )
    record = ibac.usage_audit_record(evaluate_usage(intent(ev("User", "Document", "read")), two), two)
    assert record["decision"] == "NOT_AUTHORIZED"
    assert record["skillID"] == ""
    assert record["reasonCode"] == ibac.REASON_INCORRECT_NUM_SKILLS


# --- reading mappings --------------------------------------------------------

WANT = CoazMapping(
    subject=[Entity("Person", "$token.sub")],
    resource=[Entity("Document", "$request.arguments.docId")],
    actions=[Action("read")],
)
RAW = [
    {
        "subject": [{"type": "Person", "id": "$token.sub"}],
        "resource": [{"type": "Document", "id": "$request.arguments.docId"}],
        "action": [{"name": "read"}],
    },
]


def test_read_mappings_from_an_mcp_entry_and_from_an_a2a_skill():
    mcp = {"name": "read_document", "inputSchema": {"type": "object"}, "_meta": {"x-coaz-mappings": RAW}}
    assert read_mappings(mcp) == [WANT]
    a2a = {"id": "skill-1", "name": "Skill One", "x-coaz-mappings": RAW}
    assert read_mappings(a2a) == [WANT]
    # _meta takes precedence over the entry itself.
    both = {"x-coaz-mappings": [], "_meta": {"x-coaz-mappings": RAW}}
    assert read_mappings(both) == [WANT]


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "read_document"},
        {"name": "read_document", "_meta": {"other": 1}},
        {"name": "read_document", "_meta": None},
        {"name": "read_document", "_meta": {"x-coaz-mappings": None}},
        {"name": "read_document", "_meta": {"x-coaz-mappings": []}},
    ],
)
def test_read_mappings_returns_none_without_an_error(entry):
    assert read_mappings(entry) == []


@pytest.mark.parametrize(
    ("entry", "want"),
    [
        ("not an object", "invalid field capability entry"),
        ({"_meta": "x"}, "invalid field _meta"),
        ({"_meta": {"x-coaz-mappings": {"a": 1}}}, "invalid field x-coaz-mappings: not a list"),
        ({"_meta": {"x-coaz-mappings": [1]}}, "invalid field x-coaz-mappings[0]"),
        ({"_meta": {"x-coaz-mappings": [None]}}, "invalid field x-coaz-mappings[0]: mapping is null"),
        (
            {
                "x-coaz-mappings": [
                    {"subject": [{"type": "x", "id": "a"}], "resource": [{"type": "Document", "id": "b"}]},
                ],
            },
            "invalid field x-coaz-mappings[0].subject[0].type",
        ),
        (
            {"x-coaz-mappings": [{"resource": [{"type": "Document", "id": "b"}]}]},
            "invalid field x-coaz-mappings[0].subject",
        ),
    ],
)
def test_read_mappings_rejects_what_cannot_be_read(entry, want):
    with pytest.raises(ibac.ValidationError, match=re.escape(want)):
        read_mappings(entry)


def test_capability_from_card_reads_every_skill():
    card = {
        "name": "Docs",
        "skills": [
            {"id": "a", "name": "A", "x-coaz-mappings": RAW},
            {"id": "b", "name": "B"},
        ],
    }
    cap = ibac.capability_from_card(card)
    assert cap.name == "Docs"
    assert [s.id for s in cap.skills] == ["a", "b"]
    assert cap.skills[0].mappings == [WANT]
    assert cap.skills[1].mappings == []
    assert cap.with_skill("b").skills == [cap.skills[1]]


def test_intent_from_dict_reads_the_agent_artifact():
    raw = {
        "prompt": "p",
        "evaluations": [
            {
                "subject": {"type": "User", "id": "roy"},
                "action": {"name": "CAN_RETRIEVE"},
                "resource": {"type": "Quote", "id": "stock_quote"},
                "context": {
                    "parameters": {"attribute": "price"},
                    "filters": "resource.x == 1",
                    "qualifiers": {"time": "pm"},
                },
            },
            {"subject": {"type": "User", "id": "roy"}, "resource": {"type": "Quote", "id": "stock_quote"}},
        ],
    }
    parsed = Intent.from_dict(raw)
    parsed.validate()
    assert parsed.evaluations[0].action_name == "CAN_RETRIEVE"
    assert parsed.evaluations[0].context.filter == "resource.x == 1"
    assert parsed.evaluations[1].action is None
    assert parsed.evaluations[1].as_dict() == {
        "subject": {"type": "User", "id": "roy"},
        "resource": {"type": "Quote", "id": "stock_quote"},
    }
