# Copyright (c) 2026 IndyKite
"""Turn an extracted intent into the AuthZEN calls the platform can decide today.

The Intent Agent's evaluations are AuthZEN-shaped already; three things need
care before they can be sent:

- an evaluation with no `subject.id` is a Subject Search, one with no
  `resource.id` a Resource Search, one with no `action` an Action Search -
  each has its own endpoint;
- `context.parameters` are forwarded as `input_params` only when the granting
  policy's Cypher declares them (`$name`); the platform ignores unknown
  context keys silently, so everything else is reported as *carried*, not
  pretended to be enforced;
- `filters` (CEL) and `qualifiers` are never read by the AuthZEN API, so they
  are always carried.

`plan_decisions` is pure and unit-tested; `execute_plans` sends the plans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.platform import PlatformClient, PlatformError

Kind = str  # "evaluation" | "search_subject" | "search_resource" | "search_action" | "unsupported"

_MARKERS = frozenset({"subject_source", "resource_source"})


@dataclass
class DecisionPlan:
    index: int
    kind: Kind
    endpoint: str
    request: dict[str, Any]
    input_params: dict[str, Any] = field(default_factory=dict)
    carried: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Index of the gate evaluation this plan may only run after (second-subject pattern).
    depends_on: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    skipped: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON view for the API response."""
        return {
            "index": self.index,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "request": self.request,
            "input_params": self.input_params,
            "carried": self.carried,
            "notes": self.notes,
            "depends_on": self.depends_on,
            "result": self.result,
            "error": self.error,
            "skipped": self.skipped,
        }


def _declared_params(
    policies: list[dict[str, Any]],
    subject_type: str,
    action: str | None,
    resource_type: str,
) -> set[str]:
    declared: set[str] = set()
    for policy in policies:
        if policy.get("subject_type") != subject_type or policy.get("resource_type") != resource_type:
            continue
        if action is not None and action not in (policy.get("actions") or []):
            continue
        declared.update(policy.get("params") or [])
    return declared


def _scalar(value: object) -> bool:
    """input_params take scalars only; a list or object stays carried."""
    return isinstance(value, (str, int, float, bool))


def _route(
    subject: dict[str, Any],
    resource: dict[str, Any],
    action_name: str | None,
) -> tuple[Kind, str, dict[str, Any], str | None]:
    """Pick the endpoint from which part is missing; returns (kind, endpoint, triple, unsupported-reason)."""
    has_subject_id = bool(subject.get("id"))
    has_resource_id = bool(resource.get("id"))
    if not action_name:
        triple = {"subject": subject, "resource": resource}
        if not (has_subject_id and has_resource_id):
            return "unsupported", "", triple, "an Action Search needs both an identified subject and resource"
        return "search_action", "/access/v1/search/action", triple, None
    action = {"name": action_name}
    if not has_subject_id:
        triple = {"subject": {"type": subject.get("type")}, "action": action, "resource": resource}
        if not has_resource_id:
            return "unsupported", "", triple, "a Subject Search needs an identified resource"
        return "search_subject", "/access/v1/search/subject", triple, None
    if not has_resource_id:
        triple = {"subject": subject, "action": action, "resource": {"type": resource.get("type")}}
        return "search_resource", "/access/v1/search/resource", triple, None
    return "evaluation", "/access/v1/evaluations", {"subject": subject, "action": action, "resource": resource}, None


GATE_ACTION_PREFIX = "CAN_QUERY_"


def _is_about(evaluation: dict[str, Any], subject: dict[str, Any]) -> bool:
    """Tell whether `evaluation` is somebody else's evaluation whose resource is `subject`."""
    resource = evaluation.get("resource") or {}
    who = evaluation.get("subject") or {}
    return (
        resource.get("type") == subject.get("type")
        and resource.get("id") == subject.get("id")
        and bool(who.get("id"))
        and (who.get("type"), who.get("id")) != (subject.get("type"), subject.get("id"))
    )


def _gate_for(evaluations: list[dict[str, Any]], index: int, caller: dict[str, str] | None) -> int | None:
    """Index of the gate an evaluation depends on, or None when it runs as the caller.

    The Intent Agent emits a second subject as "first the caller may ask about X, then X does Y", with the
    permission-to-query gate first. The gate is the earlier evaluation about X whose action is a query
    permission (`CAN_QUERY_*`); failing that, the first evaluation of the prompt when it is about X. Any
    other earlier evaluation that merely mentions X (a REPORTS_TO about X, say) is not a gate.
    """
    subject = evaluations[index].get("subject") or {}
    if not subject.get("id") or (
        caller and subject.get("type") == caller.get("type") and subject.get("id") == caller.get("id")
    ):
        return None
    candidates = [earlier for earlier in range(index) if _is_about(evaluations[earlier], subject)]
    for earlier in candidates:
        action = (evaluations[earlier].get("action") or {}).get("name") or ""
        if action.startswith(GATE_ACTION_PREFIX):
            return earlier
    return 0 if 0 in candidates else None


def _split_context(
    context: dict[str, Any],
    declared: set[str],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Sort the context into what the platform reads (input_params) and what is only carried, with the reasons."""
    parameters = {k: v for k, v in (context.get("parameters") or {}).items() if k not in _MARKERS}
    carried: dict[str, Any] = {}
    notes: list[str] = []
    if context.get("filters"):
        carried["filters"] = context["filters"]
        notes.append("filters (CEL) are carried for the caller to apply; the AuthZEN API does not read them")
    if context.get("qualifiers"):
        carried["qualifiers"] = context["qualifiers"]
        notes.append("qualifiers are carried; the AuthZEN API does not read them")
    input_params = {k: v for k, v in parameters.items() if k in declared and _scalar(v)}
    leftover = {k: v for k, v in parameters.items() if k not in input_params}
    if leftover:
        carried["parameters"] = leftover
        notes.append("parameters not declared as $name in a granting policy are carried, not sent")
    return input_params, carried, notes


def _plan_one(
    index: int,
    evaluations: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    caller: dict[str, str] | None,
) -> DecisionPlan:
    """Plan the AuthZEN call for evaluations[index]."""
    evaluation = evaluations[index]
    subject = dict(evaluation.get("subject") or {})
    resource = dict(evaluation.get("resource") or {})
    action = evaluation.get("action") or {}
    action_name = action.get("name") if isinstance(action, dict) else None
    declared = _declared_params(policies, subject.get("type", ""), action_name, resource.get("type", ""))
    input_params, carried, notes = _split_context(evaluation.get("context") or {}, declared)
    kind, endpoint, request, reason = _route(subject, resource, action_name)
    if reason:
        notes.append(reason)
    if input_params:
        request["context"] = {"input_params": input_params}
    depends_on = _gate_for(evaluations, index, caller)
    if depends_on is not None:
        notes.append(f"runs as a second subject: only after evaluation {depends_on + 1} (the gate) is allowed")
    return DecisionPlan(
        index=index,
        kind=kind,
        endpoint=endpoint,
        request=request,
        input_params=input_params,
        carried=carried,
        notes=notes,
        depends_on=depends_on,
    )


def plan_decisions(
    evaluations: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    caller: dict[str, str] | None = None,
) -> list[DecisionPlan]:
    """Map each evaluation to one AuthZEN request. `policies` is `platform.policy_triples` output.

    `caller` ({type, id}) lets a second-subject evaluation be tied to its gate; without it no dependency is inferred.
    """
    return [_plan_one(index, evaluations, policies, caller) for index in range(len(evaluations))]


async def _run_batch(platform: PlatformClient, plans: list[DecisionPlan]) -> None:
    """One /evaluations call for every evaluation plan in `plans`, searches one by one; errors land on the plan."""
    batch = [plan for plan in plans if plan.kind == "evaluation"]
    if batch:
        try:
            response = await platform.evaluations({"evaluations": [plan.request for plan in batch]})
            answers = response.get("evaluations") or []
            for plan, answer in zip(batch, answers, strict=False):
                plan.result = answer
        except PlatformError as exc:
            for plan in batch:
                plan.error = f"{exc.status}: {exc.body}"
    for plan in plans:
        if not plan.kind.startswith("search_"):
            continue
        try:
            plan.result = await platform.search(plan.kind.removeprefix("search_"), plan.request)
        except PlatformError as exc:
            plan.error = f"{exc.status}: {exc.body}"


def _gate_allowed(gate: DecisionPlan) -> bool:
    return gate.error is None and gate.skipped is None and bool((gate.result or {}).get("decision"))


async def execute_plans(platform: PlatformClient, plans: list[DecisionPlan]) -> list[DecisionPlan]:
    """Run the independent plans first, then each dependent plan only after its gate answered ALLOW.

    A gate that was denied, errored or never answered skips its dependents without a platform call;
    the unrelated plans of a multi-intent prompt still run.
    """
    by_index = {plan.index: plan for plan in plans}
    await _run_batch(platform, [plan for plan in plans if plan.depends_on is None])
    dependents = [plan for plan in plans if plan.depends_on is not None]
    runnable: list[DecisionPlan] = []
    for plan in dependents:
        gate = by_index.get(plan.depends_on)
        if gate is None or not _gate_allowed(gate):
            why = "denied" if gate is not None and gate.result is not None and gate.error is None else "unanswered"
            plan.skipped = f"gate (evaluation {plan.depends_on + 1}) {why}: not sent to the platform"
        else:
            runnable.append(plan)
    await _run_batch(platform, runnable)
    return plans
