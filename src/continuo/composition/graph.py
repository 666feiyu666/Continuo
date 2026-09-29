from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..model import DomainValidationError, MusicProject
from ..skills import SkillRegistry
from .plans import CompositionStage, PlanningProvider, tool_manifest


PlanningStatus = Literal["pending", "repair", "accepted", "failed"]


class PlanningGraphState(TypedDict):
    prompt: str
    active_skills: list[dict[str, str]]
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
    validation_error: str | None


class PlanningGraphRunner:
    """Run one bounded composition stage against shared project state."""

    def __init__(
        self,
        *,
        stage: CompositionStage,
        current_project: MusicProject | None,
        provider: PlanningProvider,
        renderer_name: str,
        available_instrument_ids: tuple[str, ...],
        skill_registry: SkillRegistry,
        policy_rules: tuple[str, ...],
        max_attempts: int,
        validate_response: Callable[[str, int], None],
        on_skills_resolved: Callable[[list[dict[str, str]]], None],
        on_attempt: Callable[[int, str, dict[str, Any] | None], None],
        on_rejected: Callable[[int, DomainValidationError], None],
    ) -> None:
        self.stage = stage
        self.current_project = current_project
        self.provider = provider
        self.renderer_name = renderer_name
        self.available_instrument_ids = available_instrument_ids
        self.skill_registry = skill_registry
        self.policy_rules = policy_rules
        self.max_attempts = max_attempts
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
                "active_skills": [],
                "provider_manifest": {},
                "current_response": "",
                "attempt": 0,
                "status": "pending",
                "validation_error": None,
            }
        )
        if final["status"] == "failed":
            raise DomainValidationError(
                final["validation_error"] or "composition stage rejected the model plan"
            )
        return PlanningGraphResult(
            raw_response=final["current_response"],
            attempts=final["attempt"],
            active_skills=tuple(final["active_skills"]),
            validation_error=final["validation_error"],
        )

    def _resolve_skills(self, state: PlanningGraphState) -> dict[str, Any]:
        selection = self.skill_registry.resolve(renderer_name=self.renderer_name)
        active_skills = selection.manifest()
        manifest = tool_manifest(
            self.stage,
            current_project=self.current_project,
            available_instrument_ids=self.available_instrument_ids,
        )
        manifest["rules"].extend(self.policy_rules)
        manifest["active_skills"] = active_skills
        manifest["skill_instructions"] = selection.instructions_for(
            ("conservatory-composition",)
        )
        self.on_skills_resolved(active_skills)
        return {"active_skills": active_skills, "provider_manifest": manifest}

    def _generate(self, state: PlanningGraphState) -> dict[str, Any]:
        response = self.provider.generate(state["prompt"], state["provider_manifest"])
        self.on_attempt(1, response, self._provider_audit_record())
        return {
            "current_response": response,
            "attempt": 1,
            "status": "pending",
            "validation_error": None,
        }

    def _validate(self, state: PlanningGraphState) -> dict[str, Any]:
        try:
            self.validate_response(state["current_response"], state["attempt"])
        except DomainValidationError as exc:
            self.on_rejected(state["attempt"], exc)
            repair = getattr(self.provider, "repair", None)
            can_repair = state["attempt"] < self.max_attempts and callable(repair)
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
        return {"current_response": response, "attempt": attempt, "status": "pending"}

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
