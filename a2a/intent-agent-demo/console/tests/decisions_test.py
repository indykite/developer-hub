# Copyright (c) 2026 IndyKite
import pytest
from app.decisions import execute_plans, plan_decisions
from app.platform import PlatformError

POLICIES = [
    {
        "subject_type": "User",
        "actions": ["WORKS_UNDER_DEPARTMENT"],
        "resource_type": "Department",
        "params": ["department"],
    },
    {"subject_type": "User", "actions": ["CAN_RETRIEVE", "GET_PRICE"], "resource_type": "Quote", "params": []},
    {"subject_type": "Customer", "actions": ["HAS"], "resource_type": "AccountTier", "params": ["tier"]},
]


def _ev(subject, action, resource, context=None):
    ev = {"subject": subject, "resource": resource, "context": context or {"parameters": {}}}
    if action is not None:
        ev["action"] = {"name": action}
    return ev


@pytest.mark.parametrize(
    ("evaluation", "kind", "endpoint"),
    [
        pytest.param(
            _ev({"type": "User", "id": "roy"}, "CAN_RETRIEVE", {"type": "Quote", "id": "stock_quote"}),
            "evaluation",
            "/access/v1/evaluations",
            id="identified subject and resource",
        ),
        pytest.param(
            _ev({"type": "User"}, "CAN_RETRIEVE", {"type": "Quote", "id": "stock_quote"}),
            "search_subject",
            "/access/v1/search/subject",
            id="subject search",
        ),
        pytest.param(
            _ev({"type": "User", "id": "roy"}, "CAN_RETRIEVE", {"type": "Quote"}),
            "search_resource",
            "/access/v1/search/resource",
            id="resource search",
        ),
        pytest.param(
            _ev({"type": "User", "id": "roy"}, None, {"type": "Quote", "id": "stock_quote"}),
            "search_action",
            "/access/v1/search/action",
            id="action search",
        ),
        pytest.param(
            _ev({"type": "User"}, "CAN_RETRIEVE", {"type": "Quote"}),
            "unsupported",
            "",
            id="subject search without a resource id",
        ),
    ],
)
def test_kind_follows_the_missing_part(evaluation, kind, endpoint):
    """Kind follows the missing part."""
    plan = plan_decisions([evaluation], POLICIES)[0]
    assert plan.kind == kind
    assert plan.endpoint == endpoint


def test_declared_parameters_become_input_params_and_the_rest_are_carried():
    """Declared parameters become input params and the rest are carried."""
    ev = _ev(
        {"type": "User", "id": "millicent"},
        "WORKS_UNDER_DEPARTMENT",
        {"type": "Department"},
        {"parameters": {"department": "exec", "attribute": "name", "subject_source": "caller"}},
    )
    plan = plan_decisions([ev], POLICIES)[0]
    assert plan.input_params == {"department": "exec"}
    assert plan.request["context"] == {"input_params": {"department": "exec"}}
    assert plan.carried == {"parameters": {"attribute": "name"}}
    assert "subject_source" not in str(plan.carried)


def test_filters_and_qualifiers_are_carried_never_sent():
    """Filters and qualifiers are carried never sent."""
    ev = _ev(
        {"type": "Customer", "id": "alison"},
        "VIEW_UNPAID_INVOICE",
        {"type": "Invoice"},
        {"filters": "double(resource.amount) > 500.0", "qualifiers": {"time": "today"}, "parameters": {}},
    )
    plan = plan_decisions([ev], POLICIES)[0]
    assert plan.carried["filters"] == "double(resource.amount) > 500.0"
    assert plan.carried["qualifiers"] == {"time": "today"}
    assert "context" not in plan.request
    assert len(plan.notes) == 2


def test_gate_then_second_subject_keeps_order():
    """Gate then second subject keeps order."""
    gate = _ev({"type": "Customer", "id": "rebecca"}, "CAN_QUERY_CUSTOMER", {"type": "Customer", "id": "ted"})
    second = _ev({"type": "Customer", "id": "ted"}, "HAS", {"type": "AccountTier"}, {"parameters": {"tier": "tier_3"}})
    plans = plan_decisions([gate, second], POLICIES)
    assert [p.index for p in plans] == [0, 1]
    assert plans[0].kind == "evaluation"
    assert plans[1].kind == "search_resource"
    assert plans[1].input_params == {"tier": "tier_3"}


CALLER = {"type": "Customer", "id": "rebecca"}
GATE = _ev({"type": "Customer", "id": "rebecca"}, "CAN_QUERY_CUSTOMER", {"type": "Customer", "id": "ted"})
AS_TED = _ev({"type": "Customer", "id": "ted"}, "OWNS", {"type": "Account"})
UNRELATED = _ev({"type": "Customer", "id": "rebecca"}, "OWNS", {"type": "Account"})


class FakePlatform:
    """Records the calls and answers the gate as told; searches always return one hit."""

    def __init__(self, *, gate_decision=True, gate_error=False):
        self.gate_decision = gate_decision
        self.gate_error = gate_error
        self.calls = []

    async def evaluations(self, body):
        """Answer every evaluation with the configured gate decision, or fail the batch."""
        self.calls.append(("evaluations", body))
        if self.gate_error:
            raise PlatformError(500, "boom")
        return {"evaluations": [{"decision": self.gate_decision} for _ in body["evaluations"]]}

    async def search(self, kind, body):
        """Answer every search with one result."""
        self.calls.append((f"search_{kind}", body))
        return {"results": [{"type": "Account", "id": "acc"}]}


def test_second_subject_depends_on_the_gate_that_names_it():
    """An evaluation as X depends on the earlier evaluation about X; the caller's own evaluations never do."""
    with_caller = plan_decisions([GATE, AS_TED], POLICIES, CALLER)
    assert [p.depends_on for p in with_caller] == [None, 0]
    # The gate pattern is evidence on its own: an earlier evaluation by someone else about this subject.
    without = plan_decisions([GATE, AS_TED], POLICIES)
    assert [p.depends_on for p in without] == [None, 0]
    own = plan_decisions([GATE, UNRELATED], POLICIES, CALLER)
    assert own[1].depends_on is None


def test_the_gate_is_the_query_permission_not_any_mention_of_the_subject():
    """REPORTS_TO about flo, then CAN_QUERY_USER about flo, then as flo: the dependency is the CAN_QUERY_USER one."""
    me = {"type": "User", "id": "leslie"}
    reports_to = _ev(me, "REPORTS_TO", {"type": "User", "id": "flo"})
    gate = _ev(me, "CAN_QUERY_USER", {"type": "User", "id": "flo"})
    as_flo = _ev({"type": "User", "id": "flo"}, "WORKS_IN", {"type": "Department"})
    plans = plan_decisions([reports_to, gate, as_flo], POLICIES, me)
    assert [p.depends_on for p in plans] == [None, None, 1]


@pytest.mark.asyncio
async def test_dependent_runs_only_after_the_gate_allows():
    """ALLOW on the gate lets the second-subject search run."""
    platform = FakePlatform(gate_decision=True)
    plans = await execute_plans(platform, plan_decisions([GATE, AS_TED], POLICIES, CALLER))
    assert plans[0].result == {"decision": True}
    assert plans[1].result == {"results": [{"type": "Account", "id": "acc"}]}
    assert [c[0] for c in platform.calls] == ["evaluations", "search_resource"]


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [FakePlatform(gate_decision=False), FakePlatform(gate_error=True)])
async def test_denied_or_failed_gate_skips_the_dependent_without_a_call(gate):
    """DENY or an error on the gate never reaches the platform for the second subject; unrelated plans still run."""
    plans = await execute_plans(gate, plan_decisions([GATE, AS_TED, UNRELATED], POLICIES, CALLER))
    assert plans[1].result is None
    assert plans[1].skipped is not None
    assert "search_resource" in [c[0] for c in gate.calls]  # the unrelated search ran
    assert all("ted" not in str(c[1]) for c in gate.calls if c[0] == "search_resource")
