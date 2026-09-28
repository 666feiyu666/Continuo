from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal

from ..instruments import instrument_catalog_for_prompt


ActivationMode = Literal["always", "renderer"]


def _parse_skill_document(skill_id: str) -> tuple[str, str, str]:
    resource = files(__package__).joinpath(skill_id, "SKILL.md")
    text = resource.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError(f"skill {skill_id} has invalid YAML frontmatter")
    header, body = text[4:].split("\n---\n", 1)
    frontmatter: dict[str, str] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"skill {skill_id} has invalid frontmatter")
        frontmatter[key.strip()] = value.strip().strip('"')
    if frontmatter.get("name") != skill_id:
        raise ValueError(f"skill document name does not match folder: {skill_id}")
    description = frontmatter.get("description", "")
    if not description or not body.strip():
        raise ValueError(f"skill {skill_id} is missing its description or body")
    return description, body.strip(), f"skills/{skill_id}/SKILL.md"


def _read_reference(skill_id: str, relative_path: str) -> str:
    resource = files(__package__).joinpath(skill_id, relative_path)
    return resource.read_text(encoding="utf-8").strip()


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """Versioned, trusted instructions loaded from a project skill document."""

    id: str
    version: str
    description: str
    instructions: str
    activation: ActivationMode
    source: str
    renderer_names: tuple[str, ...] = ()

    def applies_to(self, renderer_name: str) -> bool:
        if self.activation == "always":
            return True
        return renderer_name in self.renderer_names

    def manifest(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "content_sha256": hashlib.sha256(
                self.instructions.encode("utf-8")
            ).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class SkillSelection:
    skills: tuple[SkillSpec, ...]

    @property
    def instructions(self) -> str:
        return self.instructions_for(tuple(skill.id for skill in self.skills))

    def instructions_for(self, skill_ids: tuple[str, ...]) -> str:
        selected = set(skill_ids)
        blocks = []
        for skill in self.skills:
            if skill.id not in selected:
                continue
            blocks.append(
                f"Skill {skill.id} v{skill.version}:\n{skill.instructions.strip()}"
            )
        return "\n\n".join(blocks)

    def manifest(self) -> list[dict[str, str]]:
        return [skill.manifest() for skill in self.skills]


class SkillRegistry:
    """Resolve a small trusted skill set without granting new tools or authority."""

    def __init__(self, skills: tuple[SkillSpec, ...]) -> None:
        ids = [skill.id for skill in skills]
        if len(ids) != len(set(ids)):
            raise ValueError("skill ids must be unique")
        self._skills = skills

    def resolve(self, *, renderer_name: str) -> SkillSelection:
        return SkillSelection(
            tuple(
                skill
                for skill in self._skills
                if skill.applies_to(renderer_name)
            )
        )

    @classmethod
    def default(cls) -> "SkillRegistry":
        composition_description, composition_body, composition_source = (
            _parse_skill_document("conservatory-composition")
        )
        performance_description, performance_body, performance_source = (
            _parse_skill_document("expressive-performance")
        )
        soundfont_description, soundfont_body, soundfont_source = (
            _parse_skill_document("soundfont-mapping")
        )
        soundfont_contract = _read_reference(
            "soundfont-mapping",
            "references/continuo-contract.md",
        )
        return cls(
            (
                SkillSpec(
                    id="conservatory-composition",
                    version="0.3.0",
                    description=composition_description,
                    instructions=composition_body,
                    activation="always",
                    source=composition_source,
                ),
                SkillSpec(
                    id="expressive-performance",
                    version="0.1.0",
                    description=performance_description,
                    instructions=performance_body,
                    activation="renderer",
                    source=performance_source,
                    renderer_names=("fluidsynth-soundfont",),
                ),
                SkillSpec(
                    id="soundfont-mapping",
                    version="0.1.0",
                    description=soundfont_description,
                    instructions=(
                        soundfont_body
                        + "\n\nLoaded reference: references/continuo-contract.md\n\n"
                        + soundfont_contract
                        + "\n\n"
                        + instrument_catalog_for_prompt()
                    ),
                    activation="renderer",
                    source=soundfont_source,
                    renderer_names=("fluidsynth-soundfont",),
                ),
            )
        )
