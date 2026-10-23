# Copyright (c) 2026 IndyKite
import json

from app.a2a import build_send_message, parse_envelope, summarize_metrics


def test_send_message_carries_the_caller_in_metadata():
    """Send message carries the caller in metadata."""
    body = build_send_message(prompt="hi", subject_type="User", subject_id="leslie", message_id="m1")
    assert body["method"] == "SendMessage"
    assert body["params"]["message"]["role"] == "ROLE_USER"
    assert body["params"]["message"]["metadata"] == {"subject_type": "User", "subject_id": "leslie"}
    assert body["params"]["message"]["parts"] == [{"text": "hi"}]


def test_parse_envelope_reads_state_payload_and_messages():
    """Parse envelope reads state payload and messages."""
    payload = {"prompt": "hi", "evaluations": []}
    envelope = {
        "jsonrpc": "2.0",
        "id": "m1",
        "result": {
            "task": {
                "id": "t",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "history": [
                    {"role": "ROLE_USER", "parts": [{"text": "hi"}]},
                    {"role": "ROLE_AGENT", "parts": [{"text": "Extracting intent..."}]},
                ],
                "artifacts": [{"name": "result", "parts": [{"text": json.dumps(payload)}]}],
            },
        },
    }
    state, parsed, messages = parse_envelope(envelope)
    assert state == "TASK_STATE_COMPLETED"
    assert parsed == payload
    assert messages == ["Extracting intent..."]


def test_parse_envelope_without_result():
    """Parse envelope without result."""
    assert parse_envelope({"error": {"code": -32602}}) == (None, None, [])


def test_summarize_metrics_counts_requests_and_averages_latency():
    """Summarize metrics counts requests and averages latency."""
    text = (
        "# HELP http_requests_total Total\n"
        'http_requests_total{method="POST",path="/",status="200"} 3\n'
        'http_requests_total{method="POST",path="/",status="401"} 1\n'
        "http_request_duration_seconds_sum 2.0\n"
        "http_request_duration_seconds_count 4\n"
    )
    assert summarize_metrics(text) == {
        "requests_total": 4,
        "by_status": {"200": 3.0, "401": 1.0},
        "avg_latency_ms": 500,
    }
