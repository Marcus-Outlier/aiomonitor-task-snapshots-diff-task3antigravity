from __future__ import annotations


class MissingTask(KeyError):
    def __init__(self, task_id: str | int) -> None:
        super().__init__(task_id)
        self.task_id = task_id


class MissingSnapshot(KeyError):
    def __init__(self, snapshot_id: str | int) -> None:
        super().__init__(snapshot_id)
        self.snapshot_id = snapshot_id
