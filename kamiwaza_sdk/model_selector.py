"""Developer-friendly helpers for selecting the best model/variant to use."""

from __future__ import annotations

import dataclasses
import platform
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from .schemas.guide import ModelGuide, ModelVariant
from .schemas.serving.serving import UIModelDeployment

if TYPE_CHECKING:  # pragma: no cover
    from .services.models.base import ModelService


class ModelUseCase(str, Enum):
    TEXT = "text"
    VISION = "vision"
    DIFFUSION = "diffusion"
    OTHER = "other"


class PerformancePreference(str, Enum):
    BIGGER = "bigger"
    FASTER = "faster"
    BALANCED = "balanced"


class ModelIntent(str, Enum):
    RUNNING = "running"
    LAUNCH = "launch"
    AUTO = "auto"


@dataclasses.dataclass
class ModelRecommendation:
    guide: ModelGuide
    variant: ModelVariant
    intent: ModelIntent
    action: str
    deployment: Optional[UIModelDeployment] = None
    shutdown_suggestions: List[UIModelDeployment] = dataclasses.field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "guide": self.guide.model_dump(),
            "variant": self.variant.model_dump(),
            "intent": self.intent.value,
            "action": self.action,
            "deployment": self.deployment.model_dump() if self.deployment else None,
            "shutdown_suggestions": [d.model_dump() for d in self.shutdown_suggestions],
            "rationale": self.rationale,
        }


class ModelAutoSelector:
    """Curated selection helper that marries guide metadata with live deployments."""

    SIZE_RANK = {"small": 1, "medium": 2, "large": 3, "very_large": 4}
    SPEED_RANK = {"very_fast": 4, "fast": 3, "medium": 2, "slow": 1}
    USE_CASE_ALIASES = {
        ModelUseCase.TEXT: {"general use", "text", "chat", "language"},
        ModelUseCase.VISION: {"vision", "vision/multimodal", "multimodal"},
        ModelUseCase.DIFFUSION: {"diffusion", "image"},
    }

    def __init__(self, model_service: "ModelService") -> None:
        self._models = model_service
        self._client = model_service.client

    def select(
        self,
        *,
        use_case: ModelUseCase = ModelUseCase.TEXT,
        needs_reasoning: bool = False,
        performance: PerformancePreference = PerformancePreference.BALANCED,
        intent: ModelIntent = ModelIntent.AUTO,
        allow_shutdown: bool = False,
        platform_hint: Optional[str] = None,
        prefer_downloaded: bool = True,
    ) -> ModelRecommendation:
        guides = self._models.list_guides()
        deployments = self._client.serving.list_deployments()
        models = self._models.list_models(load_files=True)

        repo_by_model_id = {str(model.id): model.repo_modelId for model in models}
        running_deployments = [d for d in deployments if d.status == "DEPLOYED"]
        running_by_repo: dict[str, List[UIModelDeployment]] = {}
        for deployment in running_deployments:
            repo_id = repo_by_model_id.get(str(deployment.m_id))
            if not repo_id:
                continue
            running_by_repo.setdefault(repo_id, []).append(deployment)

        platform_name = platform_hint or self._detect_platform()

        guide_candidates = self._filter_guides(guides, use_case)
        running_candidates = [g for g in guide_candidates if g.base_model_id in running_by_repo]

        downloaded_map = self._build_downloaded_index(models)

        target_guides: Sequence[ModelGuide]
        target_intent = intent
        if intent == ModelIntent.RUNNING:
            target_guides = running_candidates
        elif intent == ModelIntent.LAUNCH:
            target_guides = guide_candidates
        else:  # AUTO
            if running_candidates:
                target_guides = running_candidates
                target_intent = ModelIntent.RUNNING
            else:
                target_guides = guide_candidates
                target_intent = ModelIntent.LAUNCH

        if not target_guides:
            raise ValueError("No guide entries matched the requested filters.")

        effective_performance = performance
        if (
            target_intent == ModelIntent.LAUNCH
            and performance == PerformancePreference.BALANCED
            and not running_candidates
        ):
            effective_performance = PerformancePreference.BIGGER

        scored = [
            (
                self._score_guide(
                    guide,
                    needs_reasoning,
                    effective_performance,
                    platform_name,
                    prefer_downloaded and target_intent == ModelIntent.LAUNCH,
                    downloaded_map,
                ),
                guide,
            )
            for guide in target_guides
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_guide = scored[0]
        best_variant, variant_reason = self._select_variant(best_guide, effective_performance, platform_name)

        deployment = None
        if best_guide.base_model_id in running_by_repo:
            deployment = running_by_repo[best_guide.base_model_id][0]

        if target_intent == ModelIntent.RUNNING and deployment is None:
            raise ValueError("No running deployments matched the requested filters.")

        action = "reuse" if deployment else "deploy"
        shutdown_suggestions: List[UIModelDeployment] = []
        if target_intent == ModelIntent.LAUNCH and allow_shutdown and running_deployments:
            shutdown_suggestions = sorted(
                running_deployments,
                key=lambda d: (d.requested_at or d.deployed_at or 0),
            )

        rationale_bits = [
            f"Guide '{best_guide.name}' (score {best_score:.1f})",
            f"Variant repo {best_variant.variant_repo} ({variant_reason})",
        ]
        if deployment:
            rationale_bits.append("Existing deployment is already running")
        elif target_intent == ModelIntent.LAUNCH:
            rationale_bits.append("Requires launching a new deployment")

        return ModelRecommendation(
            guide=best_guide,
            variant=best_variant,
            intent=target_intent,
            action=action,
            deployment=deployment,
            shutdown_suggestions=shutdown_suggestions,
            rationale="; ".join(rationale_bits),
        )

    # Internal helpers -------------------------------------------------

    def _filter_guides(self, guides: Sequence[ModelGuide], use_case: ModelUseCase) -> List[ModelGuide]:
        if use_case == ModelUseCase.OTHER:
            return list(guides)
        aliases = self.USE_CASE_ALIASES.get(use_case, {use_case.value})
        filtered = []
        for guide in guides:
            cases = guide.normalized_use_cases()
            if any(case in aliases for case in cases):
                filtered.append(guide)
        return filtered or list(guides)

    def _score_guide(
        self,
        guide: ModelGuide,
        needs_reasoning: bool,
        performance: PerformancePreference,
        platform_name: str,
        prefer_downloaded: bool,
        downloaded_map: Dict[str, bool],
    ) -> float:
        score = guide.score_average or 0.0
        if needs_reasoning:
            score += (guide.score_reasoning or guide.score_instruction_following or 0.0) * 0.5
        if performance == PerformancePreference.BIGGER:
            score += self.SIZE_RANK.get(guide.size_category.lower(), 0) * 2
        elif performance == PerformancePreference.FASTER:
            variant, _ = self._select_variant(guide, performance, platform_name)
            speed_rank = self.SPEED_RANK.get((variant.speed_rating or "medium").lower(), 2)
            score += speed_rank * 1.5
        if prefer_downloaded and downloaded_map.get(guide.base_model_id):
            score += 5.0
        return score

    def _select_variant(
        self,
        guide: ModelGuide,
        performance: PerformancePreference,
        platform_name: str,
    ) -> tuple[ModelVariant, str]:
        if not guide.variants:
            raise ValueError(f"Guide entry {guide.name} is missing variant data")

        def matches_platform(variant: ModelVariant) -> bool:
            value = variant.platform
            targets = {platform_name.lower()}
            if isinstance(value, str):
                return value.lower() in targets
            return any(item.lower() in targets for item in value)

        variants = [v for v in guide.variants if matches_platform(v)] or guide.variants

        if performance == PerformancePreference.BIGGER:
            selected = max(variants, key=lambda v: (v.minimum_vram, -(v.quality_penalty or 0)))
            reason = f"prefers higher VRAM ({selected.minimum_vram}GB)"
        elif performance == PerformancePreference.FASTER:
            selected = max(
                variants,
                key=lambda v: self.SPEED_RANK.get((v.speed_rating or "medium").lower(), 2),
            )
            reason = f"prefers speed rating {selected.speed_rating or 'medium'}"
        else:
            selected = max(
                variants,
                key=lambda v: (
                    self.SPEED_RANK.get((v.speed_rating or "medium").lower(), 2)
                    + self.SIZE_RANK.get(guide.size_category.lower(), 0)
                ),
            )
            reason = "balanced trade-off"
        return selected, reason

    def _detect_platform(self) -> str:
        system = platform.system().lower()
        if system == "darwin":
            return "mac"
        if system == "linux":
            return "gpu"
        return "cpu"

    def _build_downloaded_index(self, models: Sequence) -> Dict[str, bool]:
        index: Dict[str, bool] = {}
        for model in models:
            files = getattr(model, "m_files", None) or []
            if any(getattr(f, "download", False) for f in files):
                index[model.repo_modelId] = True
        return index
