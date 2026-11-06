# Copyright (c) 2026 IndyKite
"""Reading what a gateway answers: the agent's task, the gateway's own message, or a refusal."""

from app.gateway import GatewayAnswer, build_message, parse_answer, parse_card_answer


def answer(status, envelope=None, body="", transport_error=None):
    """Build an answer as the client would before parsing."""
    return GatewayAnswer(
        agent="billing",
        url="http://iag-billing:9903/",
        http_status=status,
        latency_ms=1,
        request={},
        request_headers={},
        body_text=body,
        envelope=envelope,
        transport_error=transport_error,
    )


def test_the_message_carries_no_subject_the_gateway_resolves_the_caller():
    """The message carries no subject: the gateway resolves the caller."""
    body = build_message(prompt="hi", message_id="m1")
    assert body["method"] == "SendMessage"
    assert "metadata" not in body["params"]["message"]
    assert body["params"]["message"]["parts"] == [{"text": "hi"}]


def test_a_forwarded_message_comes_back_as_the_agents_task():
    """A forwarded message comes back as the agent's task, reply and what it received."""
    task = {
        "id": "t",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"parts": [{"text": "Billing Agent received the message"}]}],
        "metadata": {
            "skill": "my-invoices",
            "received": {"a2a_extensions": "https://indykite.com/extensions/delegated-token/v1"},
        },
    }
    a = parse_answer(answer(200, {"jsonrpc": "2.0", "id": "1", "result": {"task": task}}))
    assert a.kind == "task"
    assert a.reply_text == "Billing Agent received the message"
    assert a.skill == "my-invoices"
    assert a.received["a2a_extensions"].endswith("/delegated-token/v1")
    # The SDK may write the task in place of the wrapper.
    assert parse_answer(answer(200, {"jsonrpc": "2.0", "id": "1", "result": task})).kind == "task"


def test_a_gateway_message_with_intent_error_is_not_a_forward():
    """A gateway message with intent_error is the gateway answering in place of the agent."""
    message = {
        "role": "ROLE_AGENT",
        "parts": [{"text": "Unable to extract intent from prompt.\nRephrase the request."}],
        "metadata": {
            "intent_error": {
                "prompt": "x",
                "error": "Unable to extract intent from prompt.",
                "reason": "r",
                "obligation": "o",
            },
        },
    }
    a = parse_answer(answer(200, {"jsonrpc": "2.0", "id": "1", "result": {"message": message}}))
    assert a.kind == "intent_error"
    assert a.intent_error["obligation"] == "o"
    assert a.reply_text.startswith("Unable to extract intent")


def test_refusals_and_errors_are_told_apart():
    """401 and 403 are refusals; everything else that is not a result is an error."""
    assert parse_answer(answer(401, {"message": "Missing bearer token"})).kind == "refused"
    refused = parse_answer(answer(403, {"message": "Authorization check failed"}))
    assert refused.kind == "refused"
    assert refused.error_text == "Authorization check failed"
    assert parse_answer(answer(502, None, body="bad gateway")).kind == "error"
    assert parse_answer(answer(None, None, transport_error="ConnectError: refused")).kind == "error"
    rpc = parse_answer(answer(200, {"jsonrpc": "2.0", "id": "1", "error": {"code": -32004, "message": "unsupported"}}))
    assert rpc.kind == "error"
    assert rpc.rpc_error["code"] == -32004
    assert parse_answer(answer(200, {"jsonrpc": "2.0", "id": "1", "result": {"odd": 1}})).kind == "error"


def test_extended_card_answer_keeps_the_skill_ids_the_gateway_left():
    """A 200 with a card: the skills the caller may use; a 403 is a refusal; anything else an error with its text."""
    card = {"name": "Billing Agent", "skills": [{"id": "my-invoices"}, {"id": "my-accounts"}]}
    ok = parse_card_answer("billing", 200, {"jsonrpc": "2.0", "id": "1", "result": card}, 12)
    assert ok.kind == "card"
    assert ok.skills == ["my-invoices", "my-accounts"]
    refused = parse_card_answer("billing", 403, {"message": "Forbidden"}, 5)
    assert (refused.kind, refused.error) == ("refused", "Forbidden")
    failed = parse_card_answer(
        "billing",
        502,
        {"jsonrpc": "2.0", "id": "1", "error": {"code": -32000, "message": "upstream"}},
        5,
    )
    assert (failed.kind, failed.error) == ("error", "upstream")
