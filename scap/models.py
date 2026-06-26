"""SCAP v2 — Core data models.

Three entities:
  Decision  — structured decision record (what, why, alternatives)
  ProjectContext — project-level state (stack, conventions, goals)
  Experience — post-hoc lesson (situation → action → lesson)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

class Decision(BaseModel):
    """A structured decision record — the core of project memory."""

    id: str = Field(default="", description="DC-YYYYMMDD-NNNN")
    project: str = Field(..., min_length=1, description="Project namespace")
    title: str = Field(..., min_length=1, description="Short decision title")
    context: str = Field(default="", description="Background / situation")
    decision: str = Field(default="", description="The chosen option")
    rationale: str = Field(default="", description="Why this option was chosen")
    alternatives: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='[{"name": "Option B", "reason_rejected": "..."}]',
    )
    constraints: List[str] = Field(
        default_factory=list,
        description='["must support 15 currencies"]',
    )
    status: Literal["active", "superseded", "deprecated"] = "active"
    superseded_by: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v:
            return v  # auto-generated later by store
        if not re.match(r"^DC-\d{8}-\d{4,}$", v):
            raise ValueError(f"Decision ID must match DC-YYYYMMDD-NNNN+, got: {v}")
        return v


# ---------------------------------------------------------------------------
# ProjectContext
# ---------------------------------------------------------------------------

class ProjectContext(BaseModel):
    """Project-level state — tech stack, conventions, active goals."""

    project: str = Field(..., min_length=1)
    tech_stack: List[str] = Field(default_factory=list)
    conventions: List[str] = Field(default_factory=list)
    active_goals: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------

class Experience(BaseModel):
    """A post-hoc lesson — what happened, what was done, what to remember."""

    id: str = Field(default="", description="EX-YYYYMMDD-NNNN")
    project: str = Field(..., min_length=1)
    situation: str = Field(default="", description="What happened")
    action: str = Field(default="", description="What was done")
    lesson: str = Field(default="", description="What to do differently")
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v:
            return v
        if not re.match(r"^EX-\d{8}-\d{4,}$", v):
            raise ValueError(f"Experience ID must match EX-YYYYMMDD-NNNN+, got: {v}")
        return v
