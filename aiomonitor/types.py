from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from backports.strenum import StrEnum


@dataclass
class FormattedLiveTaskInfo:
    task_id: str
    state: str
    name: str
    coro: str
    created_location: str
    since: str


@dataclass
class FormattedTerminatedTaskInfo:
    task_id: str
    name: str
    coro: str
    started_since: str
    terminated_since: str


@dataclass
class TaskSnapshot:
    snapshot_id: int
    name: Optional[str]
    created_at: float
    running_tasks: Sequence[FormattedLiveTaskInfo]
    terminated_tasks: Sequence[FormattedTerminatedTaskInfo]
    raw_terminated_tasks: Dict[str, TerminatedTaskInfo]

    @property
    def id(self) -> int:
        return self.snapshot_id

    @property
    def timestamp(self) -> float:
        return self.created_at

    @property
    def live_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.running_tasks


Snapshot = TaskSnapshot


class FormatItemTypes(StrEnum):
    HEADER = "header"
    CONTENT = "content"


class FormattedStackItem(NamedTuple):
    type: FormatItemTypes
    content: str


@dataclass
class TerminatedTaskInfo:
    id: str
    name: str
    coro: str
    started_at: float
    terminated_at: float
    cancelled: bool
    termination_stack: Optional[List[traceback.FrameSummary]] = None
    canceller_stack: Optional[List[traceback.FrameSummary]] = None
    exc_repr: Optional[str] = None
    persistent: bool = False


@dataclass
class CancellationChain:
    target_id: str
    canceller_id: str
    canceller_stack: Optional[List[traceback.FrameSummary]] = None
