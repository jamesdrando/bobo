from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentRole:
    name: str
    model_tier: str
    responsibilities: tuple[str, ...] = ()
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DependencyEdge:
    upstream_task_id: str
    downstream_task_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskPacket:
    task_id: str
    title: str
    assigned_role: str
    file_scope: tuple[str, ...] = ()
    function_scope: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionEvent:
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    roles: tuple[AgentRole, ...]
    tasks: tuple[TaskPacket, ...]
    dependency_edges: tuple[DependencyEdge, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "roles": [role.to_dict() for role in self.roles],
            "tasks": [task.to_dict() for task in self.tasks],
            "dependency_edges": [edge.to_dict() for edge in self.dependency_edges],
        }


class Scheduler:
    def ready_packets(
        self,
        run_spec: RunSpec,
        *,
        completed_task_ids: set[str] | None = None,
        claimed_task_ids: set[str] | None = None,
    ) -> list[TaskPacket]:
        completed = completed_task_ids or set()
        claimed = claimed_task_ids or set()
        tasks_by_id = {task.task_id: task for task in run_spec.tasks}
        dependencies: dict[str, set[str]] = {task.task_id: set(task.dependencies) for task in run_spec.tasks}
        for edge in run_spec.dependency_edges:
            if edge.downstream_task_id in tasks_by_id:
                dependencies.setdefault(edge.downstream_task_id, set()).add(edge.upstream_task_id)

        ready: list[TaskPacket] = []
        for task in run_spec.tasks:
            if task.task_id in completed or task.task_id in claimed:
                continue
            if not dependencies.get(task.task_id, set()).issubset(completed):
                continue
            if self._conflicts_with_claimed(task, tasks_by_id, claimed):
                continue
            ready.append(task)
        return ready

    def _conflicts_with_claimed(
        self,
        candidate: TaskPacket,
        tasks_by_id: dict[str, TaskPacket],
        claimed_task_ids: set[str],
    ) -> bool:
        for task_id in claimed_task_ids:
            active = tasks_by_id.get(task_id)
            if active is None:
                continue
            if set(candidate.file_scope) & set(active.file_scope):
                return True
            if set(candidate.function_scope) & set(active.function_scope):
                return True
        return False
