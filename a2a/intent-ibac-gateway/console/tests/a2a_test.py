# Copyright (c) 2026 IndyKite
from app.a2a import summarize_metrics


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
