from __future__ import annotations

import asyncio
import dataclasses
import sys
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Sequence, Tuple

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from backports.strenum import StrEnum

from aiohttp import web
from jinja2 import Environment, PackageLoader, select_autoescape
from pydantic import Field, model_validator

from ..exceptions import MissingSnapshot, MissingTask
from ..types import FormattedLiveTaskInfo, FormattedTerminatedTaskInfo
from .utils import APIParams, check_params

if TYPE_CHECKING:
    from ..monitor import Monitor


@dataclasses.dataclass
class WebUIContext:
    monitor: Monitor
    jenv: Environment


class TaskTypes(StrEnum):
    RUNNING = "running"
    TERMINATED = "terminated"


class TaskTypeParams(APIParams):
    task_type: TaskTypes = Field(default=TaskTypes.RUNNING)


class TaskIdParams(APIParams):
    task_id: str


class ListFilterParams(APIParams):
    filter: str = Field(default="")
    persistent: bool = Field(default=False)


class SnapshotCreateParams(APIParams):
    name: Optional[str] = Field(default=None)


class SnapshotIdParams(APIParams):
    snapshot_id: str


class SnapshotTaskListParams(APIParams):
    snapshot_id: str
    task_type: TaskTypes = Field(default=TaskTypes.RUNNING)
    filter: str = Field(default="")
    persistent: bool = Field(default=False)


class SnapshotTaskTraceParams(APIParams):
    snapshot_id: str
    task_id: str


class SnapshotDiffParams(APIParams):
    snapshot_id_1: str
    snapshot_id_2: Optional[str] = Field(default=None)
    filter: str = Field(default="")
    persistent: bool = Field(default=False)

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "snapshot_id_1" not in d:
                for alias in ("from_snapshot_id", "snapshot_id1", "id1", "from_id"):
                    if alias in d:
                        d["snapshot_id_1"] = d[alias]
                        break
            if "snapshot_id_2" not in d:
                for alias in ("to_snapshot_id", "snapshot_id2", "id2", "to_id"):
                    if alias in d:
                        d["snapshot_id_2"] = d[alias]
                        break
            return d
        return data


@dataclasses.dataclass
class NavigationItem:
    title: str
    current: bool


nav_menus: Mapping[str, NavigationItem] = {
    "/": NavigationItem(
        title="Dashboard",
        current=False,
    ),
    "/snapshots": NavigationItem(
        title="Snapshots",
        current=False,
    ),
    "/about": NavigationItem(
        title="About",
        current=False,
    ),
}


ctx_key = web.AppKey("ctx_key", WebUIContext)


def get_navigation_info(
    route: str,
) -> Tuple[NavigationItem, Mapping[str, NavigationItem]]:
    nav_items: Dict[str, NavigationItem] = {}
    current_item = None
    for path, item in nav_menus.items():
        is_current = path == route
        nav_items[path] = NavigationItem(title=item.title, current=is_current)
        if is_current:
            current_item = item
    if current_item is None:
        raise web.HTTPNotFound
    return current_item, nav_items


async def show_list_page(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    nav_info, nav_items = get_navigation_info(request.path)
    template = ctx.jenv.get_template("index.html")
    async with check_params(request, TaskTypeParams) as params:
        output = template.render(
            navigation=nav_items,
            page={
                "title": nav_info.title,
            },
            current_list_type=params.task_type,
            list_types=[
                {"id": TaskTypes.RUNNING, "title": "Running"},
                {"id": TaskTypes.TERMINATED, "title": "Terminated"},
            ],
        )
        return web.Response(body=output, content_type="text/html")


async def show_about_page(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    nav_info, nav_items = get_navigation_info(request.path)
    template = ctx.jenv.get_template("about.html")
    output = template.render(
        navigation=nav_items,
        page={
            "title": nav_info.title,
        },
    )
    return web.Response(body=output, content_type="text/html")


async def show_snapshots_page(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    nav_info, nav_items = get_navigation_info(request.path)
    template = ctx.jenv.get_template("snapshots.html")
    output = template.render(
        navigation=nav_items,
        page={
            "title": nav_info.title,
        },
    )
    return web.Response(body=output, content_type="text/html")


async def show_snapshot_trace_page(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    template = ctx.jenv.get_template("trace.html")
    async with check_params(request, SnapshotTaskTraceParams) as params:
        try:
            trace_data = ctx.monitor.format_snapshot_task_stack(
                params.snapshot_id, params.task_id
            )
        except (MissingSnapshot, MissingTask, KeyError, ValueError) as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )
        output = template.render(
            navigation=nav_menus,
            page={
                "title": f"Snapshot {params.snapshot_id} - Task trace for {params.task_id}",
            },
            trace_data=trace_data,
        )
        return web.Response(body=output, content_type="text/html")


async def show_trace_page(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    template = ctx.jenv.get_template("trace.html")
    async with check_params(request, TaskIdParams) as params:
        if request.path.startswith("/trace-running"):
            trace_data = ctx.monitor.format_running_task_stack(params.task_id)
        elif request.path.startswith("/trace-terminated"):
            trace_data = ctx.monitor.format_terminated_task_stack(params.task_id)
        else:
            raise RuntimeError("should not reach here")
        output = template.render(
            navigation=nav_menus,
            page={
                "title": f"Task trace for {params.task_id}",
            },
            trace_data=trace_data,
        )
        return web.Response(body=output, content_type="text/html")


async def get_version(request: web.Request) -> web.Response:
    return web.json_response(
        data={
            "value": version("aiomonitor"),
        }
    )


async def get_task_count(request: web.Request) -> web.Response:
    async with check_params(request, TaskTypeParams) as params:
        ctx: WebUIContext = request.app[ctx_key]
        if params.task_type == TaskTypes.RUNNING:
            count = len(asyncio.all_tasks(ctx.monitor._monitored_loop))
        elif params.task_type == TaskTypes.TERMINATED:
            count = len(ctx.monitor._terminated_history)
        else:
            raise RuntimeError("should not reach here")
        return web.json_response(
            data={
                "value": count,
            }
        )


async def get_live_task_list(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, ListFilterParams) as params:
        tasks = ctx.monitor.format_running_task_list(
            params.filter,
            params.persistent,
        )
        return web.json_response(
            data={
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "state": t.state,
                        "name": t.name,
                        "coro": t.coro,
                        "created_location": t.created_location,
                        "since": t.since,
                        "is_root": t.created_location == "-",
                    }
                    for t in tasks
                ]
            }
        )


async def get_terminated_task_list(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, ListFilterParams) as params:
        tasks = ctx.monitor.format_terminated_task_list(
            params.filter,
            params.persistent,
        )
        return web.json_response(
            data={
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "name": t.name,
                        "coro": t.coro,
                        "started_since": t.started_since,
                        "terminated_since": t.terminated_since,
                    }
                    for t in tasks
                ]
            }
        )


async def cancel_task(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, TaskIdParams) as params:
        try:
            coro_repr = await ctx.monitor.cancel_monitored_task(params.task_id)
            return web.json_response(
                data={
                    "msg": f"Successfully cancelled {params.task_id}",
                    "detail": coro_repr,
                },
            )
        except ValueError as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )


def _serialize_live_task(t: FormattedLiveTaskInfo) -> Dict[str, Any]:
    return {
        "task_id": t.task_id,
        "state": t.state,
        "name": t.name,
        "coro": t.coro,
        "created_location": t.created_location,
        "since": t.since,
        "is_root": t.created_location == "-",
    }


def _serialize_terminated_task(t: FormattedTerminatedTaskInfo) -> Dict[str, Any]:
    return {
        "task_id": t.task_id,
        "name": t.name,
        "coro": t.coro,
        "started_since": t.started_since,
        "terminated_since": t.terminated_since,
    }


async def create_snapshot(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, SnapshotCreateParams) as params:
        name = params.name if params.name else None
        sid = await ctx.monitor.capture_snapshot(name=name)
        snap = ctx.monitor.get_snapshot(sid)
        return web.json_response(
            data={
                "id": sid,
                "snapshot_id": sid,
                "name": snap.name,
                "running_count": snap.running_count,
                "terminated_count": snap.terminated_count,
                "termination_count": snap.termination_count,
                "msg": (
                    f"Captured snapshot {sid} ({snap.name})"
                    if snap.name
                    else f"Captured snapshot {sid}"
                ),
            }
        )


async def list_snapshots(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    summaries = ctx.monitor.list_snapshots()
    return web.json_response(
        data={
            "snapshots": [
                {
                    "id": s.snapshot_id,
                    "snapshot_id": s.snapshot_id,
                    "name": s.name,
                    "running_count": s.running_count,
                    "terminated_count": s.terminated_count,
                    "termination_count": s.termination_count,
                    "created_at": s.created_at,
                }
                for s in summaries
            ]
        }
    )


async def get_snapshot_tasks(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, SnapshotTaskListParams) as params:
        try:
            snap = ctx.monitor.get_snapshot(params.snapshot_id)
            running_tasks = ctx.monitor.format_snapshot_task_list(
                params.snapshot_id,
                params.filter,
                params.persistent,
            )
            terminated_tasks = ctx.monitor.format_snapshot_terminated_task_list(
                params.snapshot_id,
                params.filter,
                params.persistent,
            )
        except (MissingSnapshot, KeyError, ValueError) as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )
        if params.task_type == TaskTypes.TERMINATED:
            tasks_payload: Sequence[Dict[str, Any]] = [
                _serialize_terminated_task(t) for t in terminated_tasks
            ]
        else:
            tasks_payload = [_serialize_live_task(t) for t in running_tasks]
        return web.json_response(
            data={
                "snapshot_id": snap.snapshot_id,
                "name": snap.name,
                "tasks": tasks_payload,
                "running_tasks": [_serialize_live_task(t) for t in running_tasks],
                "terminated_tasks": [
                    _serialize_terminated_task(t) for t in terminated_tasks
                ],
            }
        )


async def get_snapshot_task_trace(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, SnapshotTaskTraceParams) as params:
        try:
            trace_data = ctx.monitor.format_snapshot_task_stack(
                params.snapshot_id,
                params.task_id,
            )
        except (MissingSnapshot, MissingTask, KeyError, ValueError) as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )
        items = [
            {"type": str(item.type), "content": item.content} for item in trace_data
        ]
        return web.json_response(
            data={
                "snapshot_id": int(params.snapshot_id),
                "task_id": params.task_id,
                "trace": items,
                "stack": items,
                "trace_data": items,
            }
        )


async def diff_snapshots_api(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, SnapshotDiffParams) as params:
        try:
            to_id = params.snapshot_id_2 if params.snapshot_id_2 else None
            diff = ctx.monitor.format_snapshot_diff(
                params.snapshot_id_1,
                to_id,
                filter_=params.filter,
                persistent=params.persistent,
            )
        except (MissingSnapshot, KeyError, ValueError) as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )
        return web.json_response(
            data={
                "from_snapshot_id": diff.from_snapshot_id,
                "to_snapshot_id": diff.to_snapshot_id,
                "added": [_serialize_live_task(t) for t in diff.added],
                "removed": [_serialize_live_task(t) for t in diff.removed],
                "common": [_serialize_live_task(t) for t in diff.common],
            }
        )


async def delete_snapshot_api(request: web.Request) -> web.Response:
    ctx: WebUIContext = request.app[ctx_key]
    async with check_params(request, SnapshotIdParams) as params:
        try:
            ctx.monitor.delete_snapshot(params.snapshot_id)
            return web.json_response(
                data={
                    "msg": f"Successfully deleted snapshot {params.snapshot_id}",
                    "snapshot_id": int(params.snapshot_id),
                }
            )
        except (MissingSnapshot, KeyError, ValueError) as e:
            return web.json_response(
                status=404,
                data={"msg": repr(e)},
            )


async def init_webui(monitor: Monitor) -> web.Application:
    jenv = Environment(
        loader=PackageLoader("aiomonitor.webui"), autoescape=select_autoescape()
    )
    app = web.Application()
    app[ctx_key] = WebUIContext(
        monitor=monitor,
        jenv=jenv,
    )
    app.router.add_route("GET", "/", show_list_page)
    app.router.add_route("GET", "/snapshots", show_snapshots_page)
    app.router.add_route("GET", "/about", show_about_page)
    app.router.add_route("GET", "/trace-running", show_trace_page)
    app.router.add_route("GET", "/trace-terminated", show_trace_page)
    app.router.add_route("GET", "/trace-snapshot", show_snapshot_trace_page)
    app.router.add_route("POST", "/trace-snapshot", show_snapshot_trace_page)
    app.router.add_route("GET", "/api/version", get_version)
    app.router.add_route("POST", "/api/task-count", get_task_count)
    app.router.add_route("POST", "/api/live-tasks", get_live_task_list)
    app.router.add_route("POST", "/api/terminated-tasks", get_terminated_task_list)
    app.router.add_route("DELETE", "/api/task", cancel_task)

    for prefix in ("/api/snapshots", "/api/snapshot", "/jsonapi/api/snapshots"):
        app.router.add_route("POST", prefix, create_snapshot)
        app.router.add_route("POST", f"{prefix}/", create_snapshot)
        app.router.add_route("GET", prefix, list_snapshots)
        app.router.add_route("GET", f"{prefix}/", list_snapshots)
        app.router.add_route("GET", f"{prefix}/list", list_snapshots)
        app.router.add_route("POST", f"{prefix}/list", list_snapshots)
        app.router.add_route("POST", f"{prefix}/tasks", get_snapshot_tasks)
        app.router.add_route("POST", f"{prefix}/trace", get_snapshot_task_trace)
        app.router.add_route("POST", f"{prefix}/stack", get_snapshot_task_trace)
        app.router.add_route("POST", f"{prefix}/where", get_snapshot_task_trace)
        app.router.add_route("POST", f"{prefix}/page", get_snapshot_task_trace)
        app.router.add_route("POST", f"{prefix}/diff", diff_snapshots_api)
        app.router.add_route("DELETE", prefix, delete_snapshot_api)
        app.router.add_route("DELETE", f"{prefix}/", delete_snapshot_api)

    app.router.add_static("/static", Path(__file__).parent / "static")
    return app
