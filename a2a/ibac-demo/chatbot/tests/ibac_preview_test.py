# Copyright (c) 2026 IndyKite
"""The console's IBAC discovery preview mirrors the gateway's EvaluateDiscovery rule."""

from __future__ import annotations

import app
import pytest

PERSON = [{"type": "Person"}]
CARD = {
    "name": "orchestrator_agent",
    "skills": [
        {
            "id": "query-erp",
            "name": "Query Erp",
            "x-coaz-mappings": [
                {
                    "context": {},
                    "subject": PERSON,
                    "resource": [{"type": "Invoice"}],
                    "action": [{"name": "CAN_VIEW"}, {"name": "VIEW_INVOICE"}],
                },
            ],
        },
        {
            "id": "query-retriever",
            "name": "Query Retriever",
            "x-coaz-mappings": [
                {
                    "context": {},
                    "subject": PERSON,
                    "resource": [{"type": "Quote", "id": "stock_quote"}],
                    "action": [{"name": "CAN_RETRIEVE"}],
                },
                {"context": {}, "subject": PERSON, "resource": [{"type": "Account"}], "action": []},
            ],
        },
        {"id": "web-search", "name": "Web Search"},
    ],
}


def ev(action, resource_type, subject_type="Person"):
    """Build one Intent Agent evaluation (subject, action, resource) as the gateway forwards it."""
    return {
        "subject": {"type": subject_type, "id": "auth0|x"},
        "action": {"name": action},
        "resource": {"type": resource_type},
    }


@pytest.fixture(autouse=True)
def card(monkeypatch):
    """Serve CARD as the entry agent's capability instead of fetching the agent card over HTTP."""
    skills = [
        {"id": s["id"], "name": s["name"], "x-coaz-mappings": s.get("x-coaz-mappings", [])} for s in CARD["skills"]
    ]
    monkeypatch.setattr(app, "_ibac_capability", lambda: {"name": CARD["name"], "skills": skills})


def test_match_on_types_and_action():
    """A skill matches when subject type, resource type and action all agree with one of its mappings."""
    d = app._discovery_preview([ev("CAN_VIEW", "Invoice")])
    assert d["decision"] is True
    assert d["matched_skills"] == ["query-erp"]
    assert d["per_evaluation"] == [["query-erp"]]
    assert d["reason_code"] == "intent_match_search"


def test_ids_are_not_compared_in_discovery():
    """Discovery compares types only: a mapping pinned to one resource id still matches another id."""
    d = app._discovery_preview(
        [
            {
                "subject": {"type": "Person"},
                "action": {"name": "CAN_RETRIEVE"},
                "resource": {"type": "Quote", "id": "some-other-quote"},
            },
        ],
    )
    assert d["matched_skills"] == ["query-retriever"]


def test_empty_action_list_allows_any_action():
    """A mapping without actions accepts every action on its resource type."""
    d = app._discovery_preview([ev("VIEW_DEPENDENT_ACCOUNT", "Account")])
    assert d["matched_skills"] == ["query-retriever"]


def test_mismatch_on_wrong_subject_type_or_action():
    """A wrong subject type or an action outside the mapping's list matches no skill."""
    assert app._discovery_preview([ev("CAN_VIEW", "Invoice", subject_type="User")])["decision"] is False
    assert app._discovery_preview([ev("DELETE", "Invoice")])["decision"] is False


def test_intent_without_evaluations_matches_nothing():
    """An intent that produced no evaluations is a mismatch, not an open door."""
    d = app._discovery_preview([])
    assert d["decision"] is False
    assert d["reason_code"] == "intent_mismatch"
    assert d["per_evaluation"] == []


def test_any_evaluation_matching_any_skill_is_positive():
    """One matching evaluation out of several is enough for a positive decision."""
    d = app._discovery_preview([ev("DELETE", "Invoice"), ev("VIEW_INVOICE", "Invoice")])
    assert d["decision"] is True
    assert d["per_evaluation"] == [[], ["query-erp"]]


def test_preview_off_without_card(monkeypatch):
    """Without a readable agent card the preview is absent rather than negative."""
    monkeypatch.setattr(app, "_ibac_capability", lambda: None)
    assert app._discovery_preview([ev("CAN_VIEW", "Invoice")]) is None


def test_intent_event_carries_the_preview():
    """The terminal event built from an Intent Agent record embeds the discovery preview."""
    event = app._intent_event(
        {
            "outcome": "success",
            "prompt": "Show me the invoices",
            "subject_id": "auth0|x",
            "requesting_agent_id": "abc@clients",
            "evaluations": [ev("CAN_VIEW", "Invoice")],
        },
    )
    assert event["decision"] == "INTENT_FOUND"
    assert event["discovery"]["matched_skills"] == ["query-erp"]
