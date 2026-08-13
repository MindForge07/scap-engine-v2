"""SCAP v2 — Core data models.

Four entities:
  Decision  — structured decision record (what, why, alternatives)
  ProjectContext — project-level state (stack, conventions, goals)
  Experience — post-hoc lesson (situation → action → lesson)
  LatentTrace — latent space vector + evolution metadata for a memory record
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
    # ── Memory quality (P0) ──
    importance: int = Field(
        default=3, ge=1, le=5,
        description="1-5 importance of this decision (5 = critical project fact)",
    )
    source_session: str = Field(
        default="", description="Session id that produced this record, when known",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # ── Latent space evolution fields (Phase 1) ──
    embedding: Optional[List[float]] = Field(
        default=None, description="Latent vector representation (384-dim by default)"
    )
    evolution_gen: int = Field(
        default=0, description="Evolution generation this record belongs to"
    )

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
    """Project-level state — tech stack, conventions, active goals, insights."""

    project: str = Field(..., min_length=1)
    tech_stack: List[str] = Field(default_factory=list)
    conventions: List[str] = Field(default_factory=list)
    active_goals: List[str] = Field(default_factory=list)
    # ── Reflection insights (P1): high-level takeaways distilled from decisions.
    insights: List[str] = Field(
        default_factory=list,
        description="High-level project insights distilled by scap_reflect",
    )
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
    # ── Memory quality (P0) ──
    importance: int = Field(
        default=3, ge=1, le=5,
        description="1-5 importance of this lesson (5 = critical project fact)",
    )
    source_session: str = Field(
        default="", description="Session id that produced this record, when known",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # ── Latent space evolution fields (Phase 1) ──
    embedding: Optional[List[float]] = Field(
        default=None, description="Latent vector representation (384-dim by default)"
    )
    evolution_gen: int = Field(
        default=0, description="Evolution generation this record belongs to"
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v:
            return v
        if not re.match(r"^EX-\d{8}-\d{4,}$", v):
            raise ValueError(f"Experience ID must match EX-YYYYMMDD-NNNN+, got: {v}")
        return v


# ---------------------------------------------------------------------------
# LatentTrace (Phase 1 — Latent Space Evolution)
# ---------------------------------------------------------------------------

class LatentTrace(BaseModel):
    """A latent space trace — embedding + evolution metadata for a memory record.

    Stores the vector representation of a Decision or Experience, along with
    fitness score and evolution generation. Used by the vector similarity
    search tier and the nighttime consolidation pipeline.
    """

    id: str = Field(default="", description="LT-YYYYMMDD-NNNN")
    entity_id: str = Field(..., description="Reference to Decision or Experience ID")
    entity_type: Literal["decision", "experience"] = Field(
        ..., description="Which entity type this trace belongs to"
    )
    project: str = Field(..., min_length=1, description="Project namespace")
    embedding: List[float] = Field(
        ..., description="Latent vector (384-dim by default)"
    )
    fitness: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Quality score from agent feedback (0=worst, 1=best)"
    )
    evolution_gen: int = Field(
        default=0, description="Evolution generation when this trace was created"
    )
    source_tasks: List[str] = Field(
        default_factory=list,
        description="Task IDs that contributed to this trace"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v:
            return v  # auto-generated later by store
        if not re.match(r"^LT-\d{8}-\d{4,}$", v):
            raise ValueError(f"LatentTrace ID must match LT-YYYYMMDD-NNNN+, got: {v}")
        return v
