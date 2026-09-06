from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RouteName = Literal["document", "law", "document+law", "product", "product+law", "both", "calculation", "document+calculation", "calculation+law"]
DomainName = Literal["product", "document", "law", "calculation"]
WorkerName = Literal["product", "document", "law", "calculation"]
EvidenceStatus = Literal["matched", "missing", "unresolved", "conflict"]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    intent: str
    required_domains: list[DomainName]
    entities: list[str] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    ambiguities: list[str] = Field(default_factory=list)
    response_mode: Literal["evidence_answer", "comparison", "explanation"] = "evidence_answer"


class PlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workers: list[WorkerName]
    execution_order: list[str]
    parallel_groups: list[list[WorkerName]] = Field(default_factory=list)
    tool_requirements: list[WorkerName] = Field(default_factory=list)
    direct_pdf_lookup: bool = False
    fallbacks: list[str] = Field(default_factory=list)
    expected_llm_calls: int = Field(ge=0, le=2)


class VerificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_product_count: int | None = Field(default=None, ge=1, le=10)
    risk_grade_max: int | None = Field(default=None, ge=1, le=6)
    online_only: bool = False
    sort: Literal["product_name", "total_fee", "performance"] | None = None
    require_product_evidence: bool = False
    require_pdf_evidence: bool = False
    require_law_evidence: bool = False
    require_source_version: bool = True
    allowed_evidence_statuses: list[EvidenceStatus] = Field(default_factory=lambda: ["matched"])


class ProductQueryConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    irp_only: bool = False
    risk_grade_max: int | None = Field(default=None, ge=1, le=6)
    risk_grade_min: int | None = Field(default=None, ge=1, le=6)
    online_only: bool = False
    sort_by: Literal["product_name", "total_fee", "performance"] = "product_name"
    sort_order: Literal["asc", "desc"] = "asc"
    performance_period: str | None = None
    performance_metric_type: str | None = None
    candidate_record_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=10)
    parse_issues: list[str] = Field(default_factory=list)
    name_tokens: list[str] = Field(default_factory=list)
    family_aliases: list[str] = Field(default_factory=list)
    tenors: list[str] = Field(default_factory=list)
    name_match_required: bool = False
    exact_product_names: list[str] = Field(default_factory=list)
    risk_tolerance: str | None = None
    allowed_risk_buckets: list[str] = Field(default_factory=list)
    excluded_risk_buckets: list[str] = Field(default_factory=list)
    ranking_policy: list[str] = Field(default_factory=list)


class SpecificationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RouteName
    tools: list[WorkerName]
    route_reason: str
    task_spec: TaskSpec
    plan_spec: PlanSpec
    verification_spec: VerificationSpec
    product_query_spec: ProductQueryConstraints | None = None
