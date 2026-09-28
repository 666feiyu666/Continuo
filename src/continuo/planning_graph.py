from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .domain import DomainValidationError
from .planning import PlanningProvider, tool_manifest
from .skills import SkillRegistry


PlanningStatus = Literal["pending", "repair", "accepted", "failed"]


class PlanningGraphState(TypedDict):
    prompt: str
    render_target: dict[str, Any]
    active_skills: list[dict[str, str]]
    skill_instructions: str
    provider_manifest: dict[str, Any]
    current_response: str
    attempt: int
    status: PlanningStatus
    validation_error: str | None


@dataclass(frozen=True, slots=True)
class PlanningGraphResult:
    raw_response: str
    attempts: int
    active_skills: tuple[dict[str, str], ...]
    skill_instructions: str
    validation_error: str | None


class PlanningGraphRunner:
    """LangGraph orchestration for skill resolution and bounded plan repair."""

    def __init__(
        self,
        *,
        provider: PlanningProvider,
        render_target: dict[str, Any],
        skill_registry: SkillRegistry,
        max_plan_attempts: int,
        validate_response: Callable[[str, int], None],
        on_skills_resolved: Callable[[list[dict[str, str]]], None],
        on_attempt: Callable[[int, str, dict[str, Any] | None], None],
        on_rejected: Callable[[int, DomainValidationError], None],
    ) -> None:
        self.provider = provider
        self.render_target = copy.deepcopy(render_target)
        self.skill_registry = skill_registry
        self.max_plan_attempts = max_plan_attempts
        self.validate_response = validate_response
        self.on_skills_resolved = on_skills_resolved
        self.on_attempt = on_attempt
        self.on_rejected = on_rejected

        graph = StateGraph(PlanningGraphState)
        graph.add_node("resolve_skills", self._resolve_skills)
        graph.add_node("generate", self._generate)
        graph.add_node("validate", self._validate)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "resolve_skills")
        graph.add_edge("resolve_skills", "generate")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate",
            self._route_after_validation,
            {"accepted": END, "repair": "repair", "failed": END},
        )
        graph.add_edge("repair", "validate")
        self._graph = graph.compile()

    def run(self, prompt: str) -> PlanningGraphResult:
        final = self._graph.invoke(
            {
                "prompt": prompt,
                "render_target": self.render_target,
                "active_skills": [],
                "skill_instructions": "",
                "provider_manifest": {},
                "current_response": "",
                "attempt": 0,
                "status": "pending",
                "validation_error": None,
            }
        )
        if final["status"] == "failed":
            raise DomainValidationError(
                final["validation_error"] or "planning graph rejected the model plan"
            )
        return PlanningGraphResult(
            raw_response=final["current_response"],
            attempts=final["attempt"],
            active_skills=tuple(final["active_skills"]),
            skill_instructions=final["skill_instructions"],
            validation_error=final["validation_error"],
        )

    def _resolve_skills(self, state: PlanningGraphState) -> dict[str, Any]:
        renderer_name = str(state["render_target"]["backend"])
        selection = self.skill_registry.resolve(renderer_name=renderer_name)
        active_skills = selection.manifest()
        manifest = tool_manifest()
        manifest["active_skills"] = active_skills
        manifest["skill_instructions"] = selection.instructions
        manifest["render_target"] = copy.deepcopy(state["render_target"])
        self.on_skills_resolved(active_skills)
        return {
            "active_skills": active_skills,
            "skill_instructions": selection.instructions,
            "provider_manifest": manifest,
        }

    def _generate(self, state: PlanningGraphState) -> dict[str, Any]:
        response = self.provider.generate(
            state["prompt"],
            state["provider_manifest"],
        )
        attempt = 1
        self.on_attempt(attempt, response, self._provider_audit_record())
        return {
            "current_response": response,
            "attempt": attempt,
            "status": "pending",
            "validation_error": None,
        }

    def _validate(self, state: PlanningGraphState) -> dict[str, Any]:
        try:
            self.validate_response(state["current_response"], state["attempt"])
        except DomainValidationError as exc:
            self.on_rejected(state["attempt"], exc)
            repair = getattr(self.provider, "repair", None)
            can_repair = (
                state["attempt"] < self.max_plan_attempts and callable(repair)
            )
            return {
                "status": "repair" if can_repair else "failed",
                "validation_error": str(exc),
            }
        return {"status": "accepted", "validation_error": None}

    def _repair(self, state: PlanningGraphState) -> dict[str, Any]:
        repair = getattr(self.provider, "repair", None)
        if not callable(repair):
            raise AssertionError("repair node reached without a repair-capable provider")
        response = repair(
            prompt=state["prompt"],
            previous_response=state["current_response"],
            validation_error=state["validation_error"] or "unknown validation error",
            tool_manifest=state["provider_manifest"],
        )
        attempt = state["attempt"] + 1
        self.on_attempt(attempt, response, self._provider_audit_record())
        return {
            "current_response": response,
            "attempt": attempt,
            "status": "pending",
        }

    @staticmethod
    def _route_after_validation(state: PlanningGraphState) -> PlanningStatus:
        return state["status"]

    def _provider_audit_record(self) -> dict[str, Any] | None:
        audit_record = getattr(self.provider, "audit_record", None)
        if not callable(audit_record):
            return None
        value = audit_record()
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("provider audit record must be an object")
        return copy.deepcopy(value)
