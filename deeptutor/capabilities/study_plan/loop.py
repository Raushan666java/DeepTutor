"""Chat-loop hooks for the Study Plan capability."""

from __future__ import annotations

from importlib import resources
from typing import Any

from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.capabilities.study_plan.tools import STUDY_PLAN_TOOL_NAMES
from deeptutor.core.context import UnifiedContext

STUDY_PLAN_FLAG = "study_plan_mode"


class StudyPlanLoopCapability:
    """Contribute a scheduling policy and the plan summary to the chat loop."""

    name = "study_plan"
    owned_tools: tuple[str, ...] = STUDY_PLAN_TOOL_NAMES

    def is_active(self, context: UnifiedContext) -> bool:
        return bool(context.metadata.get(STUDY_PLAN_FLAG))

    def system_block(
        self,
        context: UnifiedContext,
        *,
        language: str,
        prompts: dict[str, Any],
    ) -> PromptBlock | None:
        _ = prompts
        if not self.is_active(context):
            return None
        return PromptBlock("study_plan", _load_system_prompt(language))

    def augment_kwargs(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        context: UnifiedContext,
    ) -> dict[str, Any]:
        _ = tool_name, context
        return kwargs

    def pre_loop_seed(self, context: UnifiedContext) -> str:
        if not self.is_active(context):
            return ""
        from deeptutor.services.study_plan import get_study_plan_store

        try:
            return get_study_plan_store().injection_markdown()
        except (OSError, ValueError):
            return ""


def _load_system_prompt(language: str) -> str:
    lang = "zh" if language.lower().startswith("zh") else "en"
    prompt = resources.files(__package__).joinpath("prompts", lang, "system.md")
    return prompt.read_text(encoding="utf-8").strip()


__all__ = ["STUDY_PLAN_FLAG", "StudyPlanLoopCapability"]
