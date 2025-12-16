"""Pydantic models for the Guide/Model recommendation endpoints."""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field


StringOrStringList = Union[str, List[str]]


class ModelVariant(BaseModel):
    """Representation of a platform-specific variant described by the guide."""

    platform: StringOrStringList = Field(
        ...,
        description="Target platform(s) such as Mac, GPU, Fast CPU. Accepts a string or list.",
    )
    variant_repo: str = Field(..., description="Repository identifier for this variant.")
    variant_type: str = Field(..., description="Distribution type (gguf, mlx, awq, fp16, etc.).")
    minimum_vram: int = Field(..., description="Minimum VRAM requirement in GB (0 for CPU-only).")
    recommended_vram: Optional[int] = Field(
        default=None, description="Recommended VRAM in GB (0 for CPU-only)."
    )
    kv_ram: Optional[int] = Field(default=None, description="Estimated KV cache RAM requirement.")
    recommended_file: Optional[str] = Field(
        default=None, description="Specific artifact filename recommended for this variant."
    )
    speed_rating: Optional[str] = Field(
        default="medium", description="Speed label: very_fast, fast, medium, slow."
    )
    quality_penalty: Optional[float] = Field(
        default=None, description="Relative quality penalty (0-10 scale) vs. the base model."
    )
    notes: Optional[str] = Field(default=None, description="Variant-specific notes.")
    novice_config: Optional[dict] = Field(
        default=None, description="Optional config recommended for novice deployments."
    )


class ModelGuide(BaseModel):
    """High-level guide metadata for a model and its deployable variants."""

    id: Optional[str] = Field(default=None, description="Unique identifier for the guide entry.")
    base_model_id: str = Field(
        ..., description="Source repository identifier (e.g., mlx-community/Qwen3-4B-4bit)."
    )
    name: str = Field(..., description="Human-friendly model display name.")
    producer: str = Field(..., description="Producer of the base model (e.g., Qwen, Meta).")
    context_length: str = Field(..., description="Context length summary (4k, 8k, 32k, etc.).")
    use_case: StringOrStringList = Field(
        ..., description="One or more use-case tags (General Use, Reasoning, Vision/MultiModal, ...)."
    )
    size_category: str = Field(..., description="Size bucket: small, medium, large, very_large.")
    quality_overall: str = Field(..., description="Overall quality adjective or score bucket.")
    description: Optional[str] = Field(default=None, description="Marketing blurb for the model.")
    kamiwaza_notes: Optional[str] = Field(default=None, description="Internal notes from the Kamiwaza team.")
    guide_version: Optional[str] = Field(default=None, description="Version of the guide data.")
    variants: List[ModelVariant] = Field(
        default_factory=list, description="Available platform-specific variants."
    )

    # Benchmark scores
    score_average: Optional[float] = Field(default=None, description="Average benchmark score (0-100).")
    score_reasoning: Optional[float] = Field(default=None, description="Reasoning benchmark (0-100).")
    score_language: Optional[float] = Field(default=None, description="Language benchmark (0-100).")
    score_instruction_following: Optional[float] = Field(
        default=None, description="Instruction-following benchmark (0-100)."
    )
    score_roleplaying: Optional[float] = Field(default=None, description="Roleplaying benchmark (0-100).")
    score_agentic_coding: Optional[float] = Field(
        default=None, description="Agentic coding benchmark (0-100)."
    )
    score_coding: Optional[float] = Field(default=None, description="Coding benchmark (0-100).")
    score_math: Optional[float] = Field(default=None, description="Math benchmark (0-100).")
    score_data_analysis: Optional[float] = Field(default=None, description="Data analysis benchmark (0-100).")

    def normalized_use_cases(self) -> List[str]:
        """Return the use-case tags as a normalized, lowercase list."""

        value = self.use_case
        if value is None:
            return []
        if isinstance(value, str):
            candidates = [value]
        else:
            candidates = value
        return [c.strip().lower() for c in candidates if c]
