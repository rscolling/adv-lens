from datetime import UTC, datetime

from sqlmodel import JSON, Column, Field, SQLModel


class LLMCall(SQLModel, table=True):
    """Every LLM call is logged here — day-one requirement from CLAUDE.md.

    Kept deliberately flat: one row per call, both prompt and response as JSON
    so downstream eval and compliance tooling can diff without schema migration.
    """

    __tablename__ = "llm_calls"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True)
    node: str = Field(index=True)
    brochure_crd: str | None = Field(default=None, index=True)

    model: str
    temperature: float = 0.0
    prompt: dict = Field(sa_column=Column(JSON))
    response: dict = Field(sa_column=Column(JSON))

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class HumanReview(SQLModel, table=True):
    """HITL gate decisions. Every review writes here before a report leaves the system."""

    __tablename__ = "human_reviews"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True)
    brochure_crd: str = Field(index=True)
    reviewer: str
    decision: str  # "approved" | "rejected" | "revise"
    rationale: str
    report_hash: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
