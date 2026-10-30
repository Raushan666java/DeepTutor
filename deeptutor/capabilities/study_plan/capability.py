"""Study Plan capability — turn a goal and a deadline into dated sessions.

The model owns the conversation; the scheduler owns the dates. A plan is
created only after the learner confirms a goal, a deadline, and the hours they
can actually commit, and every session it produces can be marked. Nothing here
touches Mastery Path: that gate scores whether a learner may advance, this one
decides *when* they sit down.
"""

from __future__ import annotations

from typing import cast

from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deeptutor.capabilities.study_plan.loop import STUDY_PLAN_FLAG
from deeptutor.core.capability_protocol import (
    StreamBusProtocol,
    TurnCapability,
)
from deeptutor.core.context import UnifiedContext
from deeptutor.runtime.bootstrap.builtin_capabilities import builtin_capability_manifest
from deeptutor.runtime.stream_bus import StreamBus


class StudyPlanCapability(TurnCapability):
    """Run the chat loop with the study-plan scheduling policy and tools."""

    manifest = builtin_capability_manifest("study_plan")

    async def run(self, context: UnifiedContext, stream: StreamBusProtocol) -> None:
        context.metadata[STUDY_PLAN_FLAG] = True
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, cast(StreamBus, stream))


__all__ = ["StudyPlanCapability"]
