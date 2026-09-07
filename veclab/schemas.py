"""Strict request models; unknown fields are rejected."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, StrictFloat


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(StrictModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(StrictModel):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=500)


class ProjectUpdate(ProjectCreate):
    status: Literal["active", "archived"] = "active"


class VectorRecord(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    vector: list[StrictFloat] = Field(min_length=2, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("tags")
    @classmethod
    def check_tags(cls, value: list[str]) -> list[str]:
        cleaned = [tag.strip() for tag in value]
        if any(not tag or len(tag) > 32 for tag in cleaned):
            raise ValueError("标签长度应为1至32个字符")
        if any(";" in tag for tag in cleaned):
            raise ValueError("标签不能包含英文分号；CSV使用分号分隔标签")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("同一记录不能包含重复标签")
        return cleaned


class DatasetCreate(StrictModel):
    project_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=60)
    records: list[VectorRecord] = Field(min_length=1, max_length=64)


class DatasetImport(StrictModel):
    project_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=60)
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=1_000_000)


class DatasetGenerate(StrictModel):
    project_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=2, max_length=60)
    dimension: int = Field(default=8, ge=2, le=128, strict=True)
    count: int = Field(default=12, ge=2, le=64, strict=True)
    seed: int = Field(default=2026, ge=0, le=2**31 - 1, strict=True)


class ProfileCreate(StrictModel):
    name: str = Field(min_length=2, max_length=60)
    engine: Literal["plain", "ckks"] = "ckks"
    preset: Literal["baseline", "balanced", "compact"] = "balanced"
    metric: Literal["cosine", "dot"] = "cosine"


class RunCreate(StrictModel):
    name: str = Field(min_length=2, max_length=80)
    dataset_id: str = Field(min_length=1, max_length=40)
    profile_id: str = Field(min_length=1, max_length=40)
    query: list[StrictFloat] = Field(min_length=2, max_length=128)
    top_k: int = Field(default=5, ge=1, le=64, strict=True)
    tag: str = Field(default="", max_length=32)


class RunClone(StrictModel):
    name: str = Field(min_length=2, max_length=80)
    profile_id: str | None = Field(default=None, max_length=40)


class CompareRequest(StrictModel):
    left: str = Field(min_length=1, max_length=40)
    right: str = Field(min_length=1, max_length=40)
