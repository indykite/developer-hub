# Copyright (c) 2026 IndyKite
"""The fleet: the agent cards the IBAC engine matches every intent against, and one run over all of them.

In a deployment every agent sits behind its own Agent Gateway, and the gateway
reads the card of the agent it protects. The console keeps the cards of a small
fleet in `data/fleet.json` instead and runs the engine for each of them, so one
prompt shows which agents may serve the intent and what each would check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app import ibac
from app.decisions import MARKERS
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

# Claims of the delegation token the engine may resolve `$token.<claim>` from.
_TOKEN_CLAIMS = ("iss", "sub", "aud", "act", "jti", "iat", "exp")


class FleetAgent(BaseModel):
    """One agent of the fleet: a short id for the console, an accent for the UI, and its A2A card."""

    id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    accent: str = "grey"
    card: dict[str, Any]


class FleetFile(BaseModel):
    agents: list[FleetAgent] = Field(min_length=1)


@dataclass
class Fleet:
    """The agents and the capability the engine reads from each card."""

    agents: list[FleetAgent]
    capabilities: dict[str, ibac.Capability] = field(default_factory=dict)

    def agent(self, agent_id: str) -> FleetAgent | None:
        """Look an agent up by id."""
        return next((a for a in self.agents if a.id == agent_id), None)

    def cards(self) -> list[dict[str, Any]]:
        """Return what /api/fleet serves: each card with the mappings the engine read from it, skill by skill."""
        out = []
        for agent in self.agents:
            capability = self.capabilities[agent.id]
            out.append(
                {
                    "id": agent.id,
                    "accent": agent.accent,
                    "card": agent.card,
                    "capability": {
                        "name": capability.name,
                        "has_mappings": capability.has_mappings(),
                        "skills": [
                            {"id": s.id, "name": s.name, "mappings": [m.as_dict() for m in s.mappings]}
                            for s in capability.skills
                        ],
                    },
                },
            )
        return out

    def run(self, intent: ibac.Intent, params: dict[str, Any], options: ibac.AuditOptions) -> dict[str, Any]:
        """Run discovery over every agent, then usage on every skill a discovery matched.

        Returns the JSON view of the run: one discovery entry per agent, one usage entry per matched
        (agent, skill), the audit record of every engine call under `records`, and a summary.
        """
        discovery: list[dict[str, Any]] = []
        usage: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        for agent in self.agents:
            capability = self.capabilities[agent.id]
            response = ibac.evaluate_discovery(intent, capability)
            record = ibac.discovery_audit_record(response, capability, options)
            records.append(record)
            matched_skills: list[str] = []
            for evaluation in response.context.evaluations:
                for skill_id in evaluation.matched_skills:
                    if skill_id not in matched_skills:
                        matched_skills.append(skill_id)
            discovery.append(
                {
                    "agent": agent.id,
                    "capability": capability.name,
                    "accent": agent.accent,
                    "decision": response.decision,
                    "reason_code": response.context.reason_code,
                    "reason": response.context.reason,
                    "skill_ids": [s.id for s in capability.skills],
                    "matched_skills": matched_skills,
                    "evaluations": [
                        {"index": index, "matched_skills": list(e.matched_skills)}
                        for index, e in enumerate(response.context.evaluations)
                    ],
                    "record": record,
                },
            )
            for skill_id in matched_skills:
                entry = self.usage(agent.id, skill_id, intent, params, options)
                records.append(entry["record"])
                usage.append(entry)
        matched_agents = [d["agent"] for d in discovery if d["decision"]]
        return {
            "params": params,
            "discovery": discovery,
            "usage": usage,
            "records": records,
            "summary": {
                "agents": len(self.agents),
                "matched_agents": matched_agents,
                "matched_skills": len(usage),
                "requests": sum(len(u["requests"]) for u in usage),
            },
        }

    def usage(
        self,
        agent_id: str,
        skill_id: str,
        intent: ibac.Intent,
        params: dict[str, Any],
        options: ibac.AuditOptions,
    ) -> dict[str, Any]:
        """Run the usage flow for one skill of one agent and return it with its audit record."""
        capability = self.capabilities[agent_id].with_skill(skill_id)
        response = ibac.evaluate_usage(intent, capability, params)
        record = ibac.usage_audit_record(response, capability, options)
        generated = response.context.generated_request
        skill = capability.skills[0] if capability.skills else None
        return {
            "agent": agent_id,
            "capability": capability.name,
            "skill": skill_id,
            "skill_name": skill.name if skill else "",
            "decision": response.decision,
            "reason_code": response.context.reason_code,
            "reason": response.context.reason,
            "requests": [r.as_dict() for r in generated.authzen_requests] if generated else [],
            "record": record,
        }


def load_fleet(path: Path) -> Fleet:
    """Load and validate the fleet file: unique agent ids, every card readable by the engine."""
    data = FleetFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    seen: set[str] = set()
    capabilities: dict[str, ibac.Capability] = {}
    for agent in data.agents:
        if agent.id in seen:
            raise _bad_fleet(agent.id, "named twice")
        seen.add(agent.id)
        try:
            capability = ibac.capability_from_card(agent.card)
            capability.validate()
        except ibac.ValidationError as exc:
            raise _bad_fleet(agent.id, str(exc)) from exc
        if not capability.name:
            raise _bad_fleet(agent.id, "the card has no name")
        skill_ids = [s.id for s in capability.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise _bad_fleet(agent.id, "skill ids are not unique")
        capabilities[agent.id] = capability
    return Fleet(agents=data.agents, capabilities=capabilities)


def _bad_fleet(agent_id: str, detail: str) -> ValueError:
    return ValueError(f"fleet agent {agent_id!r}: {detail}")


def engine_params(
    persona_type: str,
    persona_id: str,
    token_claims: dict[str, Any],
    evaluations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build what the engine resolves `$references` from for one prompt.

    `token` is the delegation token's claims (so `$token.sub` is the persona), `subject` the bound
    subject, and `parameters` the union of the `context.parameters` of the intent's evaluations with
    the agent's own markers removed (so `$parameters.tier` is the tier the prompt named).
    """
    parameters: dict[str, Any] = {}
    for evaluation in evaluations:
        # The intent may come from the what-if endpoint, so nothing about its shape is trusted.
        context = evaluation.get("context") if isinstance(evaluation, dict) else None
        named = context.get("parameters") if isinstance(context, dict) else None
        if not isinstance(named, dict):
            continue
        for key, value in named.items():
            if key not in MARKERS and key not in parameters:
                parameters[key] = value
    token = {claim: token_claims[claim] for claim in _TOKEN_CLAIMS if claim in token_claims}
    if "sub" not in token:
        token["sub"] = persona_id
    return {"token": token, "subject": {"type": persona_type, "id": persona_id}, "parameters": parameters}
