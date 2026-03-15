from __future__ import annotations

import unittest

import bobo


class SchedulerTests(unittest.TestCase):
    def test_ready_packets_honor_dependencies_and_claim_conflicts(self) -> None:
        run_spec = bobo.RunSpec(
            run_id="run-1",
            roles=(
                bobo.AgentRole(name="Planner", model_tier="frontier"),
                bobo.AgentRole(name="Implementer", model_tier="cheap"),
            ),
            tasks=(
                bobo.TaskPacket(
                    task_id="t1",
                    title="First",
                    assigned_role="Implementer",
                    file_scope=("a.py",),
                    function_scope=("f1",),
                ),
                bobo.TaskPacket(
                    task_id="t2",
                    title="Second",
                    assigned_role="Implementer",
                    file_scope=("b.py",),
                    function_scope=("f2",),
                    dependencies=("t1",),
                ),
                bobo.TaskPacket(
                    task_id="t3",
                    title="Conflict",
                    assigned_role="Implementer",
                    file_scope=("a.py",),
                    function_scope=("f3",),
                ),
            ),
        )

        scheduler = bobo.Scheduler()
        first_ready = scheduler.ready_packets(run_spec)
        claimed_ready = scheduler.ready_packets(run_spec, claimed_task_ids={"t1"})
        completed_ready = scheduler.ready_packets(run_spec, completed_task_ids={"t1"})

        self.assertEqual(["t1", "t3"], [item.task_id for item in first_ready])
        self.assertEqual([], [item.task_id for item in claimed_ready])
        self.assertEqual(["t2", "t3"], [item.task_id for item in completed_ready])

    def test_orchestration_models_serialize(self) -> None:
        event = bobo.ExecutionEvent(kind="task_ready", summary="Task is ready.")
        packet = bobo.TaskPacket(
            task_id="t1",
            title="Implement feature",
            assigned_role="Implementer",
            file_scope=("bobo.py",),
            function_scope=("main",),
            acceptance_criteria=("works",),
        )
        run_spec = bobo.RunSpec(
            run_id="run-1",
            roles=(bobo.AgentRole(name="Implementer", model_tier="cheap"),),
            tasks=(packet,),
            dependency_edges=(bobo.DependencyEdge("t0", "t1"),),
        )

        self.assertEqual("task_ready", event.to_dict()["kind"])
        self.assertEqual("t1", packet.to_dict()["task_id"])
        self.assertEqual("run-1", run_spec.to_dict()["run_id"])
