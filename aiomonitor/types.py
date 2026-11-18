from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
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
    raw_terminated_tasks: Dict[str, TerminatedTaskInfo] = field(default_factory=dict)
    running_task_stacks: Dict[str, Sequence[FormattedStackItem]] = field(
        default_factory=dict
    )
    terminated_task_stacks: Dict[str, Sequence[FormattedStackItem]] = field(
        default_factory=dict
    )
    persistent_running_task_ids: Sequence[str] = field(default_factory=list)
    persistent_terminated_task_ids: Sequence[str] = field(default_factory=list)

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
    added: Sequence[FormattedLiveTaskInfo]
    removed: Sequence[FormattedLiveTaskInfo]
    common: Sequence[FormattedLiveTaskInfo]
    from_snapshot_id: Optional[int] = None
    to_snapshot_id: Optional[int] = None
    added_terminated: Sequence[FormattedTerminatedTaskInfo] = field(
        default_factory=list
    )

    @property
    def new_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.added

    @property
    def still_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.common

    @property
    def finished_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.removed

    @property
    def new_terminated_tasks(self) -> Sequence[FormattedTerminatedTaskInfo]:
        return self.added_terminated

    @property
    def added_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.added

    @property
    def removed_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.removed

    @property
    def common_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.common

    @property
    def unchanged_running_tasks(self) -> Sequence[FormattedLiveTaskInfo]:
        return self.common

    @property
    def added_terminated_tasks(self) -> Sequence[FormattedTerminatedTaskInfo]:
        return self.added_terminated


FormattedSnapshotDiff = SnapshotDiff


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
