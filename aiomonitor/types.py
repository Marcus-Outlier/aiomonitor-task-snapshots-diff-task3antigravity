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
class SnapshotSummary:
    snapshot_id: int
    name: Optional[str]
    running_count: int
    terminated_count: int
    created_at: float = 0.0

    @property
    def id(self) -> int:
        return self.snapshot_id

    @property
    def termination_count(self) -> int:
        return self.terminated_count


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

    @property
    def running_count(self) -> int:
        return len(self.running_tasks)

    @property
    def terminated_count(self) -> int:
        return len(self.terminated_tasks)

    @property
    def termination_count(self) -> int:
        return len(self.terminated_tasks)

    @property
    def summary(self) -> SnapshotSummary:
        return SnapshotSummary(
            snapshot_id=self.snapshot_id,
            name=self.name,
            running_count=len(self.running_tasks),
            terminated_count=len(self.terminated_tasks),
            created_at=self.created_at,
        )


Snapshot = TaskSnapshot


@dataclass
class SnapshotDiff:
    from_snapshot_id: int
    to_snapshot_id: Optional[int]
    new_running_tasks: Sequence[FormattedLiveTaskInfo]
    still_running_tasks: Sequence[FormattedLiveTaskInfo]
    finished_running_tasks: Sequence[FormattedLiveTaskInfo]
    new_terminated_tasks: Sequence[FormattedTerminatedTaskInfo]

    @property
    def added_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.new_running_tasks

    @property
    def removed_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.finished_running_tasks

    @property
    def unchanged_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.still_running_tasks

    @property
    def added_terminated_tasks(self) -> Sequence[FormattedTerminatedTaskInfo]:
        return self.new_terminated_tasks


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
