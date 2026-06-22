from __future__ import annotations

from dataclasses import dataclass, field


EXECUTABLE_KIND = "operation"
GOAL_KIND = "goal"
DOD_KIND = "dod"
RUNNING_STATUSES = {"queued", "running", "paused"}
TERMINAL_SUCCESS = "done"
TERMINAL_FAILURE = "blocked"
NON_BLOCKING_SOURCE_KINDS = {"scope", "context", "goal"}


class GraphRunError(ValueError):
    pass


@dataclass(frozen=True)
class GraphRunPlan:
    start_node_id: int
    node_ids: tuple[int, ...]
    goal_ids: tuple[int, ...]
    operation_ids: tuple[int, ...]
    ordered_operation_ids: tuple[int, ...]


@dataclass
class GraphRunSession:
    plan: GraphRunPlan
    running_node_id: int | None = None
    running_node_ids: set[int] = field(default_factory=set)
    paused: bool = False


@dataclass(frozen=True)
class GraphRunWait:
    node_id: int
    blocker_ids: tuple[int, ...]


class GraphRunEngine:
    def runnable_node_ids(self, state: dict) -> set[int]:
        nodes = self._nodes(state)
        runnable: set[int] = set()
        for node_id, node in nodes.items():
            kind = str(node.get("kind") or "")
            status = str(node.get("status") or "idle")
            if status in {"running", "paused"}:
                runnable.add(node_id)
                continue
            if kind == EXECUTABLE_KIND:
                runnable.add(node_id)
                continue
            if kind != GOAL_KIND:
                continue
            try:
                self.compile(state, node_id)
            except GraphRunError:
                continue
            runnable.add(node_id)
        return runnable

    def compile(self, state: dict, start_node_id: int) -> GraphRunPlan:
        nodes = self._nodes(state)
        if start_node_id not in nodes:
            raise GraphRunError("Run start node is missing.")
        start_kind = str(nodes[start_node_id].get("kind") or "")
        if start_kind == EXECUTABLE_KIND:
            return GraphRunPlan(
                start_node_id=start_node_id,
                node_ids=(start_node_id,),
                goal_ids=(),
                operation_ids=(start_node_id,),
                ordered_operation_ids=(start_node_id,),
            )
        if start_kind != GOAL_KIND:
            raise GraphRunError("Run starts from a goal.")

        reachable = self._reachable_from(state, start_node_id)
        cycle = self._cycle_in_subgraph(state, reachable)
        if cycle:
            raise GraphRunError("Run branch contains a cycle.")
        operations = tuple(
            node_id
            for node_id in sorted(reachable, key=lambda item: self._node_sort_key(nodes, item))
            if str(nodes[node_id].get("kind") or "") == EXECUTABLE_KIND
        )
        if not operations:
            raise GraphRunError("Goal has no runnable action.")
        dod_ids = tuple(
            node_id
            for node_id in sorted(reachable, key=lambda item: self._node_sort_key(nodes, item))
            if str(nodes[node_id].get("kind") or "") == DOD_KIND
        )
        if not dod_ids:
            raise GraphRunError("Goal has no DoD acceptance node.")
        goals = tuple(
            node_id
            for node_id in sorted(reachable, key=lambda item: self._node_sort_key(nodes, item))
            if str(nodes[node_id].get("kind") or "") == GOAL_KIND
        )
        ordered = self._topological_operations(state, reachable, operations)
        return GraphRunPlan(
            start_node_id=start_node_id,
            node_ids=tuple(sorted(reachable, key=lambda item: self._node_sort_key(nodes, item))),
            goal_ids=goals,
            operation_ids=operations,
            ordered_operation_ids=ordered,
        )

    def ready_operation_ids(self, state: dict, plan: GraphRunPlan) -> tuple[int, ...]:
        nodes = self._nodes(state)
        plan_nodes = set(plan.node_ids)
        ready: list[int] = []
        for node_id in plan.ordered_operation_ids:
            node = nodes.get(node_id)
            if node is None:
                continue
            if str(node.get("status") or "idle") in {
                TERMINAL_SUCCESS,
                TERMINAL_FAILURE,
                "review",
                "running",
                "paused",
            }:
                continue
            if not self.blocking_source_ids(state, plan, node_id):
                ready.append(node_id)
        return tuple(ready)

    def waiting_operations(self, state: dict, plan: GraphRunPlan) -> tuple[GraphRunWait, ...]:
        nodes = self._nodes(state)
        waits: list[GraphRunWait] = []
        for node_id in plan.ordered_operation_ids:
            node = nodes.get(node_id)
            if node is None:
                continue
            if str(node.get("status") or "idle") in {
                TERMINAL_SUCCESS,
                TERMINAL_FAILURE,
                "review",
                "running",
                "paused",
            }:
                continue
            blockers = self.blocking_source_ids(state, plan, node_id)
            if blockers:
                waits.append(GraphRunWait(node_id=node_id, blocker_ids=blockers))
        return tuple(waits)

    def blocking_source_ids(self, state: dict, plan: GraphRunPlan, node_id: int) -> tuple[int, ...]:
        nodes = self._nodes(state)
        edges = self._edges(state)
        plan_nodes = set(plan.node_ids)
        blockers: list[int] = []
        for edge in edges:
            try:
                target_id = int(edge.get("target_id"))
                source_id = int(edge.get("source_id"))
            except (TypeError, ValueError):
                continue
            if target_id != node_id or source_id not in plan_nodes:
                continue
            source = nodes.get(source_id)
            if not source:
                continue
            if str(source.get("kind") or "") in NON_BLOCKING_SOURCE_KINDS:
                continue
            status = str(source.get("status") or "idle")
            if status != TERMINAL_SUCCESS:
                blockers.append(source_id)
        return tuple(sorted(blockers, key=lambda item: self._node_sort_key(nodes, item)))

    def plan_complete(self, state: dict, plan: GraphRunPlan) -> bool:
        nodes = self._nodes(state)
        operations_done = all(
            str(nodes.get(node_id, {}).get("status") or "idle") == TERMINAL_SUCCESS
            for node_id in plan.operation_ids
        )
        if not operations_done:
            return False
        dod_ids = [
            node_id
            for node_id in plan.node_ids
            if str(nodes.get(node_id, {}).get("kind") or "") == DOD_KIND
        ]
        if not dod_ids:
            return False
        return all(str(nodes.get(node_id, {}).get("status") or "idle") == TERMINAL_SUCCESS for node_id in dod_ids)

    def _topological_operations(self, state: dict, reachable: set[int], operations: tuple[int, ...]) -> tuple[int, ...]:
        nodes = self._nodes(state)
        operation_set = set(operations)
        outgoing = {node_id: set() for node_id in operation_set}
        indegree = {node_id: 0 for node_id in operation_set}
        for edge in self._edges(state):
            try:
                source_id = int(edge.get("source_id"))
                target_id = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            if source_id in operation_set and target_id in operation_set and target_id in reachable:
                if target_id not in outgoing[source_id]:
                    outgoing[source_id].add(target_id)
                    indegree[target_id] += 1
        ready = sorted([node_id for node_id, count in indegree.items() if count == 0], key=lambda item: self._node_sort_key(nodes, item))
        ordered: list[int] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for target_id in sorted(outgoing[current], key=lambda item: self._node_sort_key(nodes, item)):
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
                    ready.sort(key=lambda item: self._node_sort_key(nodes, item))
        if len(ordered) != len(operation_set):
            raise GraphRunError("Run branch contains a cycle.")
        return tuple(ordered)

    def _reachable_from(self, state: dict, start_node_id: int) -> set[int]:
        adjacency: dict[int, list[int]] = {}
        for edge in self._edges(state):
            try:
                source_id = int(edge.get("source_id"))
                target_id = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            adjacency.setdefault(source_id, []).append(target_id)
        seen: set[int] = set()
        pending = [start_node_id]
        while pending:
            node_id = pending.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            for target_id in adjacency.get(node_id, []):
                if target_id not in seen:
                    pending.append(target_id)
        return seen

    def _cycle_in_subgraph(self, state: dict, node_ids: set[int]) -> bool:
        adjacency: dict[int, list[int]] = {node_id: [] for node_id in node_ids}
        for edge in self._edges(state):
            try:
                source_id = int(edge.get("source_id"))
                target_id = int(edge.get("target_id"))
            except (TypeError, ValueError):
                continue
            if source_id in node_ids and target_id in node_ids:
                adjacency[source_id].append(target_id)
        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(node_id: int) -> bool:
            visiting.add(node_id)
            for target_id in adjacency.get(node_id, []):
                if target_id in visiting:
                    return True
                if target_id not in visited and visit(target_id):
                    return True
            visiting.remove(node_id)
            visited.add(node_id)
            return False

        return any(visit(node_id) for node_id in sorted(node_ids) if node_id not in visited)

    @staticmethod
    def _nodes(state: dict) -> dict[int, dict]:
        nodes: dict[int, dict] = {}
        for raw in state.get("nodes") or []:
            if not isinstance(raw, dict):
                continue
            try:
                node_id = int(raw.get("id"))
            except (TypeError, ValueError):
                continue
            nodes[node_id] = raw
        return nodes

    @staticmethod
    def _edges(state: dict) -> list[dict]:
        return [edge for edge in state.get("edges") or [] if isinstance(edge, dict)]

    @staticmethod
    def _node_sort_key(nodes: dict[int, dict], node_id: int) -> tuple[float, float, int]:
        node = nodes.get(node_id, {})
        try:
            y = float(node.get("y", 0.0))
            x = float(node.get("x", 0.0))
        except (TypeError, ValueError):
            y = 0.0
            x = 0.0
        return (y, x, node_id)
