from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from typing import TypedDict

from ..config import WarehouseMap


Cell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class PlanningError(ValueError):
    message: str

    def __str__(self) -> str:
        return self.message


class RouteStep(TypedDict):
    target: str
    cell: list[int]
    shelf_id: str | None
    action: str
    path: list[list[int]]


def plan_path(
    start: Cell,
    goal: Cell,
    grid_size: Cell,
    forbidden_cells: set[Cell],
    temporary_blocked_cells: set[Cell] | None = None,
) -> list[Cell]:
    blocked = forbidden_cells | (temporary_blocked_cells or set())
    if start in blocked:
        raise PlanningError(f"start cell {start} is blocked")
    if goal in blocked:
        raise PlanningError(f"goal cell {goal} is blocked")
    if not _in_bounds(start, grid_size) or not _in_bounds(goal, grid_size):
        raise PlanningError(f"path endpoint is outside grid {grid_size}")

    frontier: list[tuple[int, int, Cell]] = []
    heappush(frontier, (0, 0, start))
    came_from: dict[Cell, Cell | None] = {start: None}
    cost_so_far: dict[Cell, int] = {start: 0}
    sequence = 0

    while frontier:
        _, _, current = heappop(frontier)
        if current == goal:
            return _reconstruct_path(came_from, current)
        for neighbor in _neighbors(current, grid_size):
            if neighbor in blocked:
                continue
            new_cost = cost_so_far[current] + 1
            if neighbor in cost_so_far and new_cost >= cost_so_far[neighbor]:
                continue
            cost_so_far[neighbor] = new_cost
            priority = new_cost + _manhattan(neighbor, goal)
            sequence += 1
            heappush(frontier, (priority, sequence, neighbor))
            came_from[neighbor] = current

    raise PlanningError(f"goal {goal} is unreachable from {start}")


def plan_patrol_route(map_config: WarehouseMap, shelf_order: list[str]) -> list[RouteStep]:
    grid_size = (map_config["grid_size"][0], map_config["grid_size"][1])
    current = (map_config["start"][0], map_config["start"][1])
    forbidden = {tuple(cell) for cell in map_config["forbidden_cells"]}
    route: list[RouteStep] = []
    for shelf_id in shelf_order:
        if shelf_id not in map_config["shelf_points"]:
            raise PlanningError(f"unknown shelf target: {shelf_id}")
        pose = map_config["shelf_points"][shelf_id]["scan_pose"]
        goal = (int(pose[0]), int(pose[1]))
        segment = plan_path(current, goal, grid_size, forbidden)
        route.append({"target": f"{shelf_id}_SCAN", "cell": [goal[0], goal[1]], "shelf_id": shelf_id, "action": "scan", "path": _json_path(segment)})
        current = goal
    home = (map_config["home"][0], map_config["home"][1])
    home_path = plan_path(current, home, grid_size, forbidden)
    route.append({"target": "HOME", "cell": [home[0], home[1]], "shelf_id": None, "action": "home", "path": _json_path(home_path)})
    return route


def _neighbors(cell: Cell, grid_size: Cell) -> list[Cell]:
    x, y = cell
    candidates = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return [candidate for candidate in candidates if _in_bounds(candidate, grid_size)]


def _in_bounds(cell: Cell, grid_size: Cell) -> bool:
    return 0 <= cell[0] < grid_size[0] and 0 <= cell[1] < grid_size[1]


def _manhattan(left: Cell, right: Cell) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _reconstruct_path(came_from: dict[Cell, Cell | None], current: Cell) -> list[Cell]:
    path = [current]
    while came_from[current] is not None:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def _json_path(path: list[Cell]) -> list[list[int]]:
    return [[cell[0], cell[1]] for cell in path]
