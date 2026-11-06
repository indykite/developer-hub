# Copyright (c) 2026 IndyKite
"""The console's mapping check: does an agent card declare a skill for an intent, and which permission is it.

An intent is what the Intent Agent extracted from a prompt: evaluations of the
form subject -> action -> resource in the graph's policy vocabulary. An agent card
can carry, on each skill, coarse-authorization mappings (`x-coaz-mappings`, as the
public ibac-demo cards do): subject types, resource types and the actions the skill
serves, with ids that are literals, empty, or `$references` into the request.

The check has two steps:

- **discovery** - every skill of a card against every evaluation, on subject type,
  resource type and action only. One skill matching one evaluation is enough; the
  answer lists the skills matched per evaluation.
- **usage** - one skill against the intent, with the ids resolved: a literal stays,
  a `$reference` is looked up in the request's parameters, an empty id means "any"
  and makes the match a search. One AuthZEN request per resolved (subject,
  resource) pair comes out, for the platform to decide.

This is the console's preview of that check, written for the demo. Nothing here
calls the network; `evaluate_discovery` and `evaluate_usage` are pure and tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NoReturn

# --- reason codes ------------------------------------------------------------

REASON_INTENT_MATCH = "intent_match"
REASON_INTENT_MATCH_SEARCH = "intent_match_search"
REASON_INTENT_MATCH_AUTHZEN_REQUIRED = "intent_match_authzen_required"
REASON_INTENT_MISMATCH = "intent_mismatch"
REASON_INVALID_MAPPING = "invalid_coaz_mapping"
REASON_MISSING_MAPPING = "missing_coaz_mapping"
REASON_INVALID_INTENT = "invalid_intent"
REASON_INVALID_CAPABILITY = "invalid_capability"
REASON_INTERNAL_ERROR = "internal_error"
REASON_INCORRECT_NUM_SKILLS = "incorrect_num_skills"

REASON_TEXT_MISSING_MAPPING = "The card publishes no mappings, so no intent can match it"
REASON_TEXT_INTENT_MISMATCH = "No skill of the card declares this intent"
REASON_TEXT_INCORRECT_NUM_SKILLS = "Usage takes exactly one skill"

# Audit vocabulary of the preview's records.
ACTION_DISCOVERY = "IBAC_DISCOVERY"
ACTION_USAGE = "IBAC_USAGE"
DECISION_AUTHORIZED = "AUTHORIZED"
DECISION_NOT_AUTHORIZED = "NOT_AUTHORIZED"

MAPPINGS_KEY = "x-coaz-mappings"
MAX_EVALUATIONS = 64
_MAX_LIST = 64
# A node type is a Cypher label: a letter or underscore, then word characters, 2 to 64 in all.
_NODE_TYPE_RE = re.compile(r"^[a-zA-Z_]\w+$")
_NODE_TYPE_MIN = 2
_NODE_TYPE_MAX = 64


class ValidationError(ValueError):
    """A field of an intent, a capability or a mapping breaks a rule; the message names the field."""


def _fail(path: str, detail: str, cause: BaseException | None = None) -> NoReturn:
    """Raise the ValidationError for one field."""
    message = f"invalid field {path}: {detail}"
    raise ValidationError(message) from cause


def _fail_too_many(path: str, what: str) -> NoReturn:
    _fail(path, f"more than {_MAX_LIST} {what}")


# --- model -------------------------------------------------------------------


@dataclass
class Entity:
    """A subject or a resource. In a mapping, `id` is a literal or a `$reference`."""

    type: str = ""
    id: str = ""

    def validate(self, path: str) -> None:
        """Require `type`, and require it to be a node type."""
        if not self.type:
            _fail(f"{path}.type", "required")
        if not (_NODE_TYPE_MIN <= len(self.type) <= _NODE_TYPE_MAX) or not _NODE_TYPE_RE.match(self.type):
            _fail(f"{path}.type", f'"{self.type}" is not a node type')

    def as_dict(self) -> dict[str, str]:
        """Return the JSON view."""
        return {"type": self.type, "id": self.id}

    @classmethod
    def from_dict(cls, raw: object) -> Entity:
        """Read `{type, id}`; anything else is an entity that fails validation."""
        if not isinstance(raw, dict):
            return cls()
        return cls(type=_text(raw.get("type")), id=_text(raw.get("id")))


@dataclass
class Action:
    """An action, by name."""

    name: str = ""

    def validate(self, path: str) -> None:
        """Require `name`."""
        if not self.name:
            _fail(f"{path}.name", "required")

    @classmethod
    def from_dict(cls, raw: object) -> Action:
        """Read `{name}`."""
        return cls(name=_text(raw.get("name"))) if isinstance(raw, dict) else cls()


@dataclass
class EvaluationContext:
    parameters: dict[str, Any] = field(default_factory=dict)
    qualifiers: dict[str, Any] = field(default_factory=dict)
    input_params: dict[str, Any] = field(default_factory=dict)
    filter: str = ""

    def validate(self, path: str) -> None:
        """Bound each map to 64 entries."""
        for name, value in (
            ("parameters", self.parameters),
            ("qualifiers", self.qualifiers),
            ("input_params", self.input_params),
        ):
            if len(value) > _MAX_LIST:
                _fail_too_many(f"{path}.{name}", "entries")

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view, empty members left out."""
        out: dict[str, Any] = {}
        if self.parameters:
            out["parameters"] = self.parameters
        if self.qualifiers:
            out["qualifiers"] = self.qualifiers
        if self.input_params:
            out["input_params"] = self.input_params
        if self.filter:
            out["filter"] = self.filter
        return out

    @classmethod
    def from_dict(cls, raw: object) -> EvaluationContext:
        """Read the context of an Intent Agent evaluation (`filters` and `filter` are both accepted)."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            parameters=_mapping(raw.get("parameters")),
            qualifiers=_mapping(raw.get("qualifiers")),
            input_params=_mapping(raw.get("input_params")),
            filter=_text(raw.get("filter") or raw.get("filters")),
        )


@dataclass
class Evaluation:
    """One access check of an intent: subject and resource are required, the action may be absent."""

    subject: Entity | None = None
    resource: Entity | None = None
    action: Action | None = None
    context: EvaluationContext | None = None

    def validate(self, path: str = "evaluation") -> None:
        """Require a valid subject and resource; an action, when present, must have a name."""
        if self.subject is None:
            _fail(f"{path}.subject", "required")
        self.subject.validate(f"{path}.subject")
        if self.resource is None:
            _fail(f"{path}.resource", "required")
        self.resource.validate(f"{path}.resource")
        if self.action is not None:
            self.action.validate(f"{path}.action")
        if self.context is not None:
            self.context.validate(f"{path}.context")

    @property
    def action_name(self) -> str:
        """Return the action's name, empty when the evaluation has none (an Action Search)."""
        return self.action.name if self.action else ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view in the AuthZEN shape."""
        out: dict[str, Any] = {
            "subject": self.subject.as_dict() if self.subject else None,
            "resource": self.resource.as_dict() if self.resource else None,
        }
        if self.action is not None:
            out["action"] = {"name": self.action.name}
        if self.context is not None and (ctx := self.context.as_dict()):
            out["context"] = ctx
        return out

    @classmethod
    def from_dict(cls, raw: object) -> Evaluation:
        """Read an evaluation as the Intent Agent emits it."""
        if not isinstance(raw, dict):
            return cls()
        return cls(
            subject=Entity.from_dict(raw["subject"]) if "subject" in raw else None,
            resource=Entity.from_dict(raw["resource"]) if "resource" in raw else None,
            action=Action.from_dict(raw["action"]) if raw.get("action") is not None else None,
            context=EvaluationContext.from_dict(raw["context"]) if raw.get("context") is not None else None,
        )


@dataclass
class Intent:
    """A prompt and the evaluations derived from it."""

    prompt: str = ""
    evaluations: list[Evaluation] = field(default_factory=list)

    def validate(self) -> None:
        """Bound the evaluations to 64 and require each to be valid."""
        if len(self.evaluations) > MAX_EVALUATIONS:
            _fail_too_many("evaluations", "evaluations")
        for index, evaluation in enumerate(self.evaluations):
            evaluation.validate(f"evaluations[{index}]")

    @classmethod
    def from_dict(cls, raw: object) -> Intent:
        """Read `{prompt, evaluations}` - the artifact of a completed Intent Agent task."""
        if not isinstance(raw, dict):
            return cls()
        evaluations = raw.get("evaluations")
        return cls(
            prompt=_text(raw.get("prompt")),
            evaluations=[Evaluation.from_dict(e) for e in evaluations] if isinstance(evaluations, list) else [],
        )


@dataclass
class CoazMapping:
    """A coarse-authorization mapping: subjects, resources and the actions allowed between them.

    No action means every action.
    """

    subject: list[Entity] = field(default_factory=list)
    resource: list[Entity] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def validate(self, path: str = "mapping") -> None:
        """Require a subject and a resource, every entity a node type, every action named."""
        if len(self.context) > _MAX_LIST:
            _fail_too_many(f"{path}.context", "entries")
        for name, entities in (("subject", self.subject), ("resource", self.resource)):
            if not entities:
                _fail(f"{path}.{name}", "at least one entity is required")
            if len(entities) > _MAX_LIST:
                _fail_too_many(f"{path}.{name}", "entities")
            for index, entity in enumerate(entities):
                entity.validate(f"{path}.{name}[{index}]")
        if len(self.actions) > _MAX_LIST:
            _fail_too_many(f"{path}.action", "actions")
        for index, action in enumerate(self.actions):
            action.validate(f"{path}.action[{index}]")

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view in the `x-coaz-mappings` shape."""
        return {
            "subject": [e.as_dict() for e in self.subject],
            "resource": [e.as_dict() for e in self.resource],
            "action": [{"name": a.name} for a in self.actions],
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, raw: object) -> CoazMapping:
        """Read one mapping; `action` is the list key on the wire."""
        if not isinstance(raw, dict):
            _fail("mapping", "not an object")
        return cls(
            subject=[Entity.from_dict(e) for e in _listing(raw.get("subject"))],
            resource=[Entity.from_dict(e) for e in _listing(raw.get("resource"))],
            actions=[Action.from_dict(a) for a in _listing(raw.get("action"))],
            context=_mapping(raw.get("context")),
        )


@dataclass
class Skill:
    """A skill of an agent card, reduced to what IBAC reads: id, name and mappings."""

    id: str = ""
    name: str = ""
    mappings: list[CoazMapping] = field(default_factory=list)

    def validate(self, path: str = "skill") -> None:
        """Require an id and a name, and every mapping to be valid."""
        if not self.id:
            _fail(f"{path}.id", "required")
        if not self.name:
            _fail(f"{path}.name", "required")
        if len(self.mappings) > _MAX_LIST:
            _fail_too_many(f"{path}.{MAPPINGS_KEY}", "mappings")
        for index, mapping in enumerate(self.mappings):
            mapping.validate(f"{path}.{MAPPINGS_KEY}[{index}]")


@dataclass
class Capability:
    """What an agent can do: its name and its skills."""

    name: str = ""
    skills: list[Skill] = field(default_factory=list)

    def validate(self) -> None:
        """Bound the skills to 64 and require each to be valid."""
        if len(self.skills) > _MAX_LIST:
            _fail_too_many("skills", "skills")
        for index, skill in enumerate(self.skills):
            skill.validate(f"skills[{index}]")

    def has_mappings(self) -> bool:
        """Tell whether at least one skill carries a mapping; without one no intent can match."""
        return any(skill.mappings for skill in self.skills)

    def with_skill(self, skill_id: str) -> Capability:
        """Reduce the capability to one skill - what the usage flow takes."""
        return Capability(name=self.name, skills=[s for s in self.skills if s.id == skill_id])


# --- reading mappings from a card or an MCP entry -----------------------------


def read_mappings(entry: object) -> list[CoazMapping]:
    """Return the mappings of one capability entry: an A2A skill or an MCP tool, resource or prompt.

    An A2A skill keeps them under `x-coaz-mappings` in the entry itself, an MCP entry in its
    `_meta`, which takes precedence when it holds the key. No mappings is not an error; a mapping
    that cannot be read or is not valid is.
    """
    if not isinstance(entry, dict):
        _fail("capability entry", "not an object")
    raw = entry.get(MAPPINGS_KEY)
    meta = entry.get("_meta")
    if meta is not None:
        if not isinstance(meta, dict):
            _fail("_meta", "not an object")
        if MAPPINGS_KEY in meta:
            raw = meta[MAPPINGS_KEY]
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list):
        _fail(MAPPINGS_KEY, "not a list")
    return [_read_mapping(item, f"{MAPPINGS_KEY}[{index}]") for index, item in enumerate(raw)]


def _read_mapping(item: object, path: str) -> CoazMapping:
    if item is None:
        _fail(path, "mapping is null")
    try:
        mapping = CoazMapping.from_dict(item)
        mapping.validate(path)
    except ValidationError as exc:
        message = str(exc)
        if message.startswith(f"invalid field {path}"):
            raise
        _fail(path, message.removeprefix("invalid field "), exc)
    return mapping


def skill_from_card(entry: dict[str, Any]) -> Skill:
    """Build a Skill from an agent-card skill entry, mappings included."""
    return Skill(id=_text(entry.get("id")), name=_text(entry.get("name")), mappings=read_mappings(entry))


def capability_from_card(card: dict[str, Any]) -> Capability:
    """Build the Capability of an A2A agent card: its name and its skills with their mappings."""
    skills = card.get("skills")
    return Capability(
        name=_text(card.get("name")),
        skills=[skill_from_card(s) for s in skills if isinstance(s, dict)] if isinstance(skills, list) else [],
    )


# --- responses ---------------------------------------------------------------


@dataclass
class ResponseEvaluation:
    """One evaluation of the intent and the ids of the skills that matched it, in capability order."""

    evaluation: Evaluation
    matched_skills: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        return {"evaluation": self.evaluation.as_dict(), "matched_skills": list(self.matched_skills)}


@dataclass
class GeneratedRequest:
    """The AuthZEN requests a usage match produced, for one skill."""

    skill_id: str
    authzen_requests: list[Evaluation] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        return {"skill_id": self.skill_id, "authzen_requests": [r.as_dict() for r in self.authzen_requests]}


@dataclass
class ResponseContext:
    reason: str = ""
    reason_code: str = ""
    evaluations: list[ResponseEvaluation] = field(default_factory=list)
    generated_request: GeneratedRequest | None = None


@dataclass
class Response:
    """The outcome of an IBAC evaluation: a decision and the context behind it."""

    decision: bool
    context: ResponseContext

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        ctx = self.context
        return {
            "decision": self.decision,
            "reason": ctx.reason,
            "reason_code": ctx.reason_code,
            "evaluations": [e.as_dict() for e in ctx.evaluations],
            "generated_request": ctx.generated_request.as_dict() if ctx.generated_request else None,
        }


def _negative(reason_code: str, reason: str = "", evaluations: list[ResponseEvaluation] | None = None) -> Response:
    return Response(
        decision=False,
        context=ResponseContext(reason=reason, reason_code=reason_code, evaluations=evaluations or []),
    )


def _validate_capability(capability: Capability) -> Response | None:
    """Refuse an invalid capability or one without mappings; return None when it can be evaluated."""
    try:
        capability.validate()
    except ValidationError as exc:
        return _negative(REASON_INVALID_CAPABILITY, str(exc))
    if not capability.has_mappings():
        return _negative(REASON_MISSING_MAPPING, REASON_TEXT_MISSING_MAPPING)
    return None


# --- matching ----------------------------------------------------------------


def _entity_type_match(intent_entity: Entity | None, mapping_entities: list[Entity]) -> bool:
    intent_type = intent_entity.type if intent_entity else ""
    return any(intent_type == e.type for e in mapping_entities)


def _find_matching_entities(intent_entity: Entity | None, mapping_entities: list[Entity]) -> list[Entity]:
    intent_type = intent_entity.type if intent_entity else ""
    return [e for e in mapping_entities if e.type == intent_type]


def _action_match(intent_action: Action | None, mapping_actions: list[Action]) -> bool:
    """Allow every action for a mapping without actions, otherwise require the name to be listed."""
    if not mapping_actions:
        return True
    name = intent_action.name if intent_action else ""
    return any(name == a.name for a in mapping_actions)


def _mapping_matches_discovery(evaluation: Evaluation, mapping: CoazMapping) -> bool:
    """Match the subject type, the resource type and the action; ids are not compared."""
    return (
        _entity_type_match(evaluation.subject, mapping.subject)
        and _entity_type_match(evaluation.resource, mapping.resource)
        and _action_match(evaluation.action, mapping.actions)
    )


def evaluate_discovery(intent: Intent, capability: Capability) -> Response:
    """Run the discovery flow: which skills of the capability satisfy which evaluations of the intent.

    Positive when at least one skill satisfies at least one evaluation. Evaluations no skill
    satisfies do not cause a mismatch; an intent without evaluations matches no skill.
    """
    try:
        intent.validate()
    except ValidationError as exc:
        return _negative(REASON_INVALID_INTENT, str(exc))
    if (refused := _validate_capability(capability)) is not None:
        return refused

    response_evaluations: list[ResponseEvaluation] = []
    any_match = False
    for evaluation in intent.evaluations:
        matched = [
            skill.id
            for skill in capability.skills
            if any(_mapping_matches_discovery(evaluation, m) for m in skill.mappings)
        ]
        any_match = any_match or bool(matched)
        response_evaluations.append(ResponseEvaluation(evaluation=evaluation, matched_skills=matched))

    if not any_match:
        return _negative(REASON_INTENT_MISMATCH, REASON_TEXT_INTENT_MISMATCH, response_evaluations)
    return Response(
        decision=True,
        context=ResponseContext(reason_code=REASON_INTENT_MATCH_SEARCH, evaluations=response_evaluations),
    )


def resolve_key(key: str, params: dict[str, Any] | None) -> tuple[str, bool]:
    """Resolve a mapping id: a `$reference` is looked up in params, anything else is a literal."""
    if not key.startswith("$"):
        return key, True
    return lookup_value(key[1:], params or {})


def lookup_value(key: str, params: dict[str, Any]) -> tuple[str, bool]:
    """Look `key` up in params; `a.b.c` walks nested maps. Missing, non-string or empty values are not found."""
    if key in params:
        value = params[key]
        if isinstance(value, str) and value == "":
            return "", False
        return (value, True) if isinstance(value, str) else ("", False)
    head, sep, rest = key.partition(".")
    if not sep:
        return "", False
    nested = params.get(head)
    if not isinstance(nested, dict):
        return "", False
    return lookup_value(rest, nested)


def _resolve_entities(entities: list[Entity], params: dict[str, Any] | None) -> list[Entity]:
    """Copy the entities with their ids resolved; drop an entity whose id cannot be resolved."""
    resolved: list[Entity] = []
    for entity in entities:
        value, ok = resolve_key(entity.id, params)
        if ok:
            resolved.append(Entity(type=entity.type, id=value))
    return resolved


def _mapping_requests_usage(
    evaluation: Evaluation,
    mapping: CoazMapping,
    params: dict[str, Any] | None,
) -> list[Evaluation]:
    """Build one request per resolved (subject, resource) pair when the mapping matches the evaluation."""
    subjects = _find_matching_entities(evaluation.subject, mapping.subject)
    resources = _find_matching_entities(evaluation.resource, mapping.resource)
    if not (subjects and resources and _action_match(evaluation.action, mapping.actions)):
        return []
    return [
        Evaluation(
            subject=subject,
            resource=resource,
            action=evaluation.action,
            context=EvaluationContext(input_params=dict(params or {})),
        )
        for subject in _resolve_entities(subjects, params)
        for resource in _resolve_entities(resources, params)
    ]


def _skill_requests_usage(
    evaluation: Evaluation,
    skill: Skill,
    params: dict[str, Any] | None,
) -> tuple[list[Evaluation], bool]:
    """Collect the complete requests of every matching mapping, and whether any mapping matched at all.

    A match can come with no requests, for example when an id is an empty literal.
    """
    candidates: list[Evaluation] = []
    for mapping in skill.mappings:
        candidates.extend(_mapping_requests_usage(evaluation, mapping, params))
    complete = [r for r in candidates if r.subject and r.subject.id and r.resource and r.resource.id and r.action_name]
    return complete, bool(candidates)


def evaluate_usage(intent: Intent, capability: Capability, params: dict[str, Any] | None = None) -> Response:
    """Run the usage flow: confirm the intent against the single skill a discovery selected.

    Positive when at least one evaluation matches the skill. The response then carries the AuthZEN
    requests to check (`intent_match_authzen_required`), or none when the match needs a search
    instead (`intent_match_search`). `params` resolves the `$references` in the mapping ids.
    """
    try:
        intent.validate()
    except ValidationError as exc:
        return _negative(REASON_INVALID_INTENT, str(exc))
    if (refused := _validate_capability(capability)) is not None:
        return refused
    if len(capability.skills) != 1:
        return _negative(REASON_INCORRECT_NUM_SKILLS, REASON_TEXT_INCORRECT_NUM_SKILLS)

    skill = capability.skills[0]
    requests: list[Evaluation] = []
    matched = False
    for evaluation in intent.evaluations:
        found, ok = _skill_requests_usage(evaluation, skill, params)
        requests.extend(found)
        matched = matched or ok

    if not matched:
        return _negative(REASON_INTENT_MISMATCH)
    if requests:
        return Response(
            decision=True,
            context=ResponseContext(
                reason_code=REASON_INTENT_MATCH_AUTHZEN_REQUIRED,
                generated_request=GeneratedRequest(skill_id=skill.id, authzen_requests=requests),
            ),
        )
    return Response(decision=True, context=ResponseContext(reason_code=REASON_INTENT_MATCH_SEARCH))


# --- audit records -----------------------------------------------------------


@dataclass(frozen=True)
class AuditOptions:
    """What the engine does not know and the caller adds to a record: the prompt, the subject, the chain."""

    prompt: str = ""
    subject: str = ""
    actors_chain: tuple[str, ...] = ()
    service: str = "ibac-engine"


def _record(response: Response, action: str, capability: Capability, options: AuditOptions) -> dict[str, Any]:
    return {
        "decision": DECISION_AUTHORIZED if response.decision else DECISION_NOT_AUTHORIZED,
        "reason": response.context.reason,
        "subject": options.subject,
        "actor": "",
        "actorsChain": list(options.actors_chain),
        "action": action,
        "service": options.service,
        "timestamp": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "traceID": "",
        "prompt": options.prompt,
        "capabilityName": capability.name,
    }


def discovery_audit_record(
    response: Response,
    capability: Capability,
    options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Build the audit record of a discovery: the skill ids in capability order, the matches per evaluation."""
    record = _record(response, ACTION_DISCOVERY, capability, options or AuditOptions())
    record["skillIDs"] = [s.id for s in capability.skills]
    record["evaluations"] = [
        {
            "subjectType": e.evaluation.subject.type if e.evaluation.subject else "",
            "resourceType": e.evaluation.resource.type if e.evaluation.resource else "",
            "action": e.evaluation.action_name,
            "matchedSkills": list(e.matched_skills),
        }
        for e in response.context.evaluations
    ]
    return record


def usage_audit_record(
    response: Response,
    capability: Capability,
    options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Build the audit record of a usage: the skill id (empty unless exactly one skill) and the reason code."""
    record = _record(response, ACTION_USAGE, capability, options or AuditOptions())
    record["skillID"] = capability.skills[0].id if len(capability.skills) == 1 else ""
    record["reasonCode"] = response.context.reason_code
    return record


# --- helpers -----------------------------------------------------------------


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _listing(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []
