"""The contract between evidence, a diagnosis provider, and what gets stored.

A provider (the built-in rules, or an AI model behind an adapter) receives `DiagnosisInput` and
must return `DiagnosisOutput`. Everything a provider returns is untrusted until it has passed this
schema and the checks in `validate.py`: unknown fields are rejected, text is length-capped, and
every claim must cite evidence ids that really exist in the input.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Category = Literal[
    "frontend_error", "backend_api", "network", "accessibility", "performance", "content", "other"
]
Severity = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
Source = Literal["browser_observed", "api_observed", "customer_supplied"]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
EvidenceId = Annotated[str, StringConstraints(min_length=8, max_length=64)]


class EvidenceItem(BaseModel):
    id: str
    type: Literal["observation", "api_check_run", "log_entry"]
    source: Source
    kind: str
    severity: str = "info"
    page_url: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class CorrelationItem(BaseModel):
    observation_id: str
    classification: Literal["backend_confirmed", "uncertain", "no_backend_data", "third_party"]
    needs_review: bool
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)


class DiagnosisInput(BaseModel):
    site_host: str
    telemetry_available: bool
    evidence: list[EvidenceItem]
    correlations: list[CorrelationItem] = Field(default_factory=list)


class Fact(BaseModel):
    """Something that was observed. Must cite the evidence that shows it."""

    model_config = ConfigDict(extra="forbid")
    text: Text
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=20)


class Hypothesis(BaseModel):
    """An inference. Shown to people as inferred, never as fact."""

    model_config = ConfigDict(extra="forbid")
    text: Text
    confidence: Confidence
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=20)


class IssueDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    category: Category
    severity: Severity
    component: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)] = ""
    facts: list[Fact] = Field(default_factory=list, max_length=12)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=6)
    unverified: list[ShortText] = Field(default_factory=list, max_length=6)
    reproduction_steps: list[ShortText] = Field(default_factory=list, max_length=10)
    impact: Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)] = ""
    proposed_fix: Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)] = ""
    verification_plan: Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)] = ""
    confidence: Confidence
    classification: Literal["diagnosed", "needs_review"]
    needs_review_reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None
    ) = None
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=50)


class DiagnosisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[IssueDraft] = Field(default_factory=list, max_length=30)


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderResult(BaseModel):
    output: DiagnosisOutput
    usage: Usage = Field(default_factory=Usage)
    model: str
