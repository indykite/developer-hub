# Copyright (c) 2026 IndyKite
"""The prompt gallery and personas - demo data, validated once at startup."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path


class Persona(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["User", "Customer"]
    name: str
    role: str
    facts: list[str] = Field(default_factory=list)


class Prompt(BaseModel):
    persona: str
    text: str = Field(min_length=1)
    expect: str = ""
    auth_mode: Literal["delegation", "raw_subject", "wrong_audience", "none"] = "delegation"


class Category(BaseModel):
    id: str
    title: str
    blurb: str
    kind: Literal["capability", "guardrail"] = "capability"
    prompts: list[Prompt]


class Gallery(BaseModel):
    personas: list[Persona]
    categories: list[Category]

    def persona(self, persona_id: str) -> Persona | None:
        """Look a persona up by id."""
        return next((p for p in self.personas if p.id == persona_id), None)


def load_gallery(path: Path) -> Gallery:
    """Load and validate the gallery file; every prompt must name a known persona."""
    gallery = Gallery.model_validate(json.loads(path.read_text(encoding="utf-8")))
    known = {p.id for p in gallery.personas}
    for category in gallery.categories:
        for prompt in category.prompts:
            if prompt.persona not in known:
                msg = f"prompt in {category.id!r} names unknown persona {prompt.persona!r}"
                raise ValueError(msg)
    return gallery
