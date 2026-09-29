from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class CrawlOptions(BaseModel):
    max_pages: int = Field(default=3, ge=1, le=8)
    max_depth: int = Field(default=1, ge=0, le=2)
    deadline_seconds: int = Field(default=20, ge=2, le=45)


class JobRequest(BaseModel):
    url: str
    schema_: dict[str, Any] = Field(alias="schema")
    allowed_hostnames: list[str] = Field(default_factory=list, max_length=10)
    page_hints: list[str] = Field(default_factory=list, max_length=10)
    options: CrawlOptions = Field(default_factory=CrawlOptions)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("schema_")
    @classmethod
    def valid_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - {"type", "properties", "required", "title", "description"}:
            raise ValueError("unsupported schema keyword")
        props = value.get("properties")
        if value.get("type") != "object" or not isinstance(props, dict) or not 3 <= len(props) <= 10:
            raise ValueError("schema must be an object with 3 to 10 properties")
        required = value.get("required", [])
        if (not isinstance(required, list) or any(not isinstance(item, str) for item in required)
                or len(required) != len(set(required)) or not set(required) <= set(props)):
            raise ValueError("required must be a list of unique property names")
        for name, spec in props.items():
            if not isinstance(name, str) or not name or len(name) > 80 or not isinstance(spec, dict):
                raise ValueError("invalid property")
            if spec.get("type") not in {"string", "number", "integer", "boolean"}:
                raise ValueError("only scalar properties are supported")
            if set(spec) - {"type", "title", "description", "x-unit", "x-currency"}:
                raise ValueError("unsupported property keyword")
            if "title" in spec and (not isinstance(spec["title"], str) or not 1 <= len(spec["title"].strip()) <= 80):
                raise ValueError("property title must be a nonempty string up to 80 characters")
            if "description" in spec and not isinstance(spec["description"], str):
                raise ValueError("property description must be a string")
            if "x-unit" in spec and (spec["type"] not in {"number", "integer"} or spec["x-unit"] not in {"month", "year", "day", "user", "seat", "GB"}):
                raise ValueError("x-unit must be a supported numeric unit")
            if "x-currency" in spec and (spec["type"] not in {"number", "integer"} or spec["x-currency"] not in {"USD", "EUR", "GBP", "INR"}):
                raise ValueError("x-currency must be a supported numeric currency")
        return value


class Evidence(BaseModel):
    source_url: str
    fetched_at: str
    excerpt: str
    locator: str
    snapshot_hash: str
    label: str | None = None
    raw_value: str | None = None


class Candidate(BaseModel):
    value: Any
    evidence: Evidence
    unit: str | None = None
    currency: str | None = None
    value_type: Literal["string", "number", "integer", "boolean"] | None = None
    reason: str | None = None


class FieldResult(BaseModel):
    state: Literal["verified", "missing", "conflicting", "blocked", "unverified"]
    value: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    reason: str | None = None
    unit: str | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def verified_has_evidence(self):
        if self.state == "verified" and (self.value is None or not self.evidence):
            raise ValueError("verified field needs value and evidence")
        return self


class Result(BaseModel):
    schema_version: str = "1.0"
    extraction_version: str = "0.2.0"
    job_id: str
    status: Literal["complete", "partial", "failed"]
    requested_url: str
    observed_at: str
    fields: dict[str, FieldResult]
    pages: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    usage: dict[str, Any]
