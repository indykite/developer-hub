# Copyright (c) 2026 IndyKite
"""The invoice search runs with the action of the intent the delegation token carries."""

from __future__ import annotations

import pytest
import server


def _evaluation(action: str | None, resource: str = "Invoice", subject: str = "Person") -> dict:
    evaluation = {"subject": {"type": subject}, "resource": {"type": resource}}
    if action:
        evaluation["action"] = {"name": action}
    return evaluation


def _claims(*hops: list[dict] | None) -> dict:
    """Claims whose act chain has one hop per argument, newest first; None is a hop without an intent."""
    act: dict | None = None
    for evaluations in reversed(hops):
        hop: dict = {"sub": "agent@clients"}
        if evaluations is not None:
            hop["intent"] = {"evaluations": evaluations}
        if act is not None:
            hop["act"] = act
        act = hop
    return {"sub": "auth0|person", "act": act} if act else {"sub": "auth0|person"}


def test_no_intent_searches_with_the_default_action():
    """A token without an intent keeps the ERP's own action."""
    assert server._invoice_actions(_claims()) == [server.ERP_ACTION]
    assert server._invoice_actions(_claims(None, None)) == [server.ERP_ACTION]


def test_intent_action_narrows_the_search():
    """An intent about unpaid invoices searches for those, not for everything viewable."""
    claims = _claims(None, [_evaluation("VIEW_UNPAID_INVOICE")])
    assert server._invoice_actions(claims) == ["VIEW_UNPAID_INVOICE"]


def test_newest_intent_in_the_chain_wins():
    """The hop closest to this server that carries an intent is the current one."""
    claims = _claims([_evaluation("VIEW_INVOICE")], [_evaluation("VIEW_UNPAID_INVOICE")])
    assert server._invoice_actions(claims) == ["VIEW_INVOICE"]


def test_every_invoice_action_of_the_intent_is_kept_once():
    """Several evaluations about invoices give several actions, in the intent's order, without repeats."""
    claims = _claims([_evaluation("VIEW_UNPAID_INVOICE"), _evaluation("CAN_VIEW"), _evaluation("VIEW_UNPAID_INVOICE")])
    assert server._invoice_actions(claims) == ["VIEW_UNPAID_INVOICE", "CAN_VIEW"]


@pytest.mark.parametrize(
    "evaluation",
    [
        _evaluation("CAN_READ", resource="Weather"),
        _evaluation("DELETE_INVOICE"),
        _evaluation(None),
        _evaluation("CAN_VIEW", subject="Agent"),
        {"subject": "Person", "resource": {"type": "Invoice"}, "action": {"name": "CAN_VIEW"}},
    ],
)
def test_an_intent_about_something_else_keeps_the_default_action(evaluation):
    """Only an evaluation of the ERP's subject type, an invoice and an advertised action selects the action."""
    assert server._invoice_actions(_claims([evaluation])) == [server.ERP_ACTION]


def test_search_asks_once_per_action_and_joins_the_ids(monkeypatch):
    """The allowed set is what any of the actions allows, each invoice once."""
    asked = []
    answers = {"VIEW_INVOICE": ["inv-1", "inv-2"], "VIEW_UNPAID_INVOICE": ["inv-2", "inv-3"]}

    def fake_search(subject_id, action):
        asked.append((subject_id, action))
        return answers[action]

    monkeypatch.setattr(server, "_search_invoice_ids", fake_search)
    allowed = server._allowed_invoice_ids("auth0|person", ["VIEW_INVOICE", "VIEW_UNPAID_INVOICE"])
    assert allowed == ["inv-1", "inv-2", "inv-3"]
    assert asked == [("auth0|person", "VIEW_INVOICE"), ("auth0|person", "VIEW_UNPAID_INVOICE")]


def test_tools_publish_their_mappings():
    """Both tools name the three invoice actions; get_invoice also names the ids the gateway resolves."""
    names = [{"name": name} for name in server.INVOICE_ACTION_NAMES]
    assert server.LIST_INVOICES_MAPPINGS[0]["action"] == names
    mapping = server.GET_INVOICE_MAPPINGS[0]
    assert mapping["action"] == names
    assert mapping["subject"] == [{"type": server.ERP_SUBJECT_TYPE, "id": "$token.sub"}]
    assert mapping["resource"] == [{"type": server.ERP_RESOURCE_TYPE, "id": "$request.arguments.invoice_id"}]
