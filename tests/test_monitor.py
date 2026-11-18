from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import io
import sys
import unittest.mock
from typing import Sequence

import click
import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

import aiomonitor.termui.commands
from aiomonitor import Monitor, start_monitor
from aiomonitor.termui.commands import (
    auto_command_done,
    command_done,
    current_monitor,
    current_stdout,
    custom_help_option,
    monitor_cli,
    print_ok,
)


@contextlib.contextmanager
def monitor_common():
    def make_baz():
        return "baz"

    locals_ = {"foo": "bar", "make_baz": make_baz}
    # In the tests, we reuse the pytest's event loop as the monitored loop.
    # Because of this, all cross-loop coroutine invocations should use the following
    # pattern in both tests and the monitor/termui implementation:
    # > fut = asyncio.wrap_future(asyncio.run_coroutine_threadsafe(...))
    # > await fut
    test_loop = asyncio.get_running_loop()
    mon = Monitor(test_loop, locals=locals_)
    with mon:
        yield mon


@pytest.fixture
async def monitor(request, event_loop):
    with monitor_common() as monitor_instance:
        yield monitor_instance


def get_task_ids(event_loop):
    return [id(t) for t in asyncio.all_tasks(loop=event_loop)]


class BufferedOutput(DummyOutput):
    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def write(self, data: str) -> None:
        self._buffer.write(data)

    def write_raw(self, data: str) -> None:
        self._buffer.write(data)


async def invoke_command(
    monitor: Monitor,
    args: Sequence[str],
) -> str:
    dummy_stdout = BufferedOutput()
    current_monitor_token = current_monitor.set(monitor)
    current_stdout_token = current_stdout.set(dummy_stdout._buffer)

    async def _ui_create_event() -> asyncio.Event:
        return asyncio.Event()

    fut = asyncio.run_coroutine_threadsafe(_ui_create_event(), monitor._ui_loop)
    command_done_event: asyncio.Event = await asyncio.wrap_future(fut)
    command_done_token = command_done.set(command_done_event)
    try:
        with unittest.mock.patch.object(
            aiomonitor.termui.commands,
            "print_formatted_text",
            functools.partial(
                aiomonitor.termui.commands.print_formatted_text, output=dummy_stdout
            ),
        ):
            ctx = contextvars.copy_context()
            ctx.run(
                monitor_cli.main,
                args,
                prog_name="",
                obj=monitor,
                standalone_mode=False,  # type: ignore
            )
            # If Click raises UsageError before running the command,
            # there will be no one to set command_done_event.
            # In this case, the error is propagated to the upper stack
            # immediately here.
            fut = asyncio.run_coroutine_threadsafe(
                command_done_event.wait(),  # type: ignore
                monitor._ui_loop,
            )
            await asyncio.wrap_future(fut)
    finally:
        command_done.reset(command_done_token)
        current_stdout.reset(current_stdout_token)
        current_monitor.reset(current_monitor_token)
    with contextlib.closing(dummy_stdout._buffer):
        return dummy_stdout._buffer.getvalue()


@pytest.fixture(params=[True, False], ids=["console:True", "console:False"])
def console_enabled(request):
    return request.param


@pytest.mark.asyncio
async def test_ctor(event_loop, console_enabled):
    with Monitor(event_loop, console_enabled=console_enabled):
        await asyncio.sleep(0.01)
    with start_monitor(event_loop, console_enabled=console_enabled) as m:
        await asyncio.sleep(0.01)
    assert m.closed

    m = Monitor(event_loop, console_enabled=console_enabled)
    m.start()
    try:
        await asyncio.sleep(0.01)
    finally:
        m.close()
        m.close()  # make sure call is idempotent
    assert m.closed

    m = Monitor(event_loop, console_enabled=console_enabled)
    m.start()
    with m:
        await asyncio.sleep(0.01)
    assert m.closed

    # make sure that monitor inside async func can exit correctly
    with Monitor(event_loop, console_enabled=console_enabled):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_basic_monitor(event_loop, monitor: Monitor):
    resp = await invoke_command(monitor, ["help"])
    assert "Commands:" in resp

    with pytest.raises(click.UsageError):
        await invoke_command(monitor, ["xxx"])

    resp = await invoke_command(monitor, ["ps"])
    assert "Task" in resp

    with pytest.raises(click.UsageError):
        await invoke_command(monitor, ["ps", "123"])

    resp = await invoke_command(monitor, ["signal", "name"])
    assert "Unknown signal" in resp

    resp = await invoke_command(monitor, ["stacktrace"])
    assert "self.run_forever()" in resp

    resp = await invoke_command(monitor, ["w", "123"])
    assert "No task 123" in resp

    resp = await invoke_command(monitor, ["where", "123"])
    assert "No task 123" in resp

    with pytest.raises(click.UsageError):
        await invoke_command(monitor, ["c", "123"])

    resp = await invoke_command(monitor, ["cancel", "123"])
    assert "Invalid or non-existent task ID" in resp

    resp = await invoke_command(monitor, ["ca", "123"])
    assert "Invalid or non-existent task ID" in resp


myvar = contextvars.ContextVar("myvar", default=42)


@pytest.mark.asyncio
async def test_monitor_task_factory(event_loop):
    async def do():
        await asyncio.sleep(0)
        myself = asyncio.current_task()
        assert myself is not None
        assert myself.get_name() == "mytask"

    with Monitor(event_loop, console_enabled=False, hook_task_factory=True):
        t = asyncio.create_task(do(), name="mytask")
        await t


@pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="The context argument of asyncio.create_task() is added in Python 3.11",
)
@pytest.mark.asyncio
async def test_monitor_task_factory_with_context():
    ctx = contextvars.Context()
    # This context is bound at the outermost scope,
    # and inside it the initial value of myvar is kept intact.

    async def do():
        await asyncio.sleep(0)
        assert myvar.get() == 42  # we are referring the outer context
        myself = asyncio.current_task()
        assert myself is not None
        assert myself.get_name() == "mytask"

    myvar.set(99)  # override in the current task's context
    event_loop = asyncio.get_running_loop()
    with Monitor(event_loop, console_enabled=False, hook_task_factory=True):
        t = asyncio.create_task(do(), name="mytask", context=ctx)
        await t
    assert myvar.get() == 99


@pytest.mark.asyncio
async def test_cancel_where_tasks(
    monitor: Monitor,
) -> None:
    async def sleeper():
        await asyncio.sleep(100)  # xxx

    test_loop = monitor._monitored_loop
    t = test_loop.create_task(sleeper())
    t_id = id(t)
    await asyncio.sleep(0.1)

    task_ids = get_task_ids(test_loop)
    assert len(task_ids) > 0
    assert t_id in task_ids
    resp = await invoke_command(monitor, ["where", str(t_id)])
    assert "Task" in resp
    resp = await invoke_command(monitor, ["cancel", str(t_id)])
    assert "Cancelled task" in resp
    assert t.done()


@pytest.mark.asyncio
async def test_monitor_with_console(monitor: Monitor) -> None:
    with create_pipe_input() as pipe_input:
        stdout_buf = BufferedOutput()
        with create_app_session(input=pipe_input, output=stdout_buf):

            async def _interact():
                await asyncio.sleep(0.2)
                try:
                    pipe_input.send_text("await asyncio.sleep(0.1, result=333)\r\n")
                    pipe_input.flush()
                    await asyncio.sleep(0.1)
                    pipe_input.send_text("foo\r\n")
                    pipe_input.flush()
                    await asyncio.sleep(0.4)
                    resp = stdout_buf._buffer.getvalue()
                    assert "This console is running in an asyncio event loop." in resp
                    assert "333" in resp
                    assert "bar" in resp
                finally:
                    pipe_input.send_text("exit()\r\n")

            t = asyncio.create_task(_interact())
            await invoke_command(monitor, ["console"])
            await t
    # Check if we are back to the original shell.
    resp = await invoke_command(monitor, ["help"])
    assert "Commands" in resp


@pytest.mark.asyncio
async def test_custom_monitor_command(monitor: Monitor):
    @monitor_cli.command(name="something")
    @click.argument("arg")
    @custom_help_option
    @auto_command_done
    def do_something(ctx: click.Context, arg: str) -> None:
        print_ok(f"doing something with {arg}")

    resp = await invoke_command(monitor, ["something", "someargument"])
    assert "doing something with someargument" in resp


@pytest.mark.asyncio
async def test_snapshots(event_loop):
    async def sleeper():
        await asyncio.sleep(100)

    async def short_task():
        await asyncio.sleep(0.01)

    with Monitor(event_loop, console_enabled=False, hook_task_factory=True) as mon:
        t1 = asyncio.create_task(sleeper(), name="sleeper_task")
        t2 = asyncio.create_task(short_task(), name="short_task")
        await t2
        await asyncio.sleep(0.05)  # allow termination queue to process

        sid0 = await mon.capture_snapshot("first")
        assert sid0 == 0
        snap0 = mon.get_snapshot(sid0)
        assert snap0.snapshot_id == 0
        assert snap0.name == "first"
        assert any(t.name == "sleeper_task" for t in snap0.running_tasks)
        assert any(t.name == "short_task" for t in snap0.terminated_tasks)

        t1.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t1
        await asyncio.sleep(0.05)

        sid1 = await mon.capture_snapshot()
        assert sid1 == 1
        snap1 = mon.get_snapshot(sid1)
        assert snap1.name is None
        assert not any(t.name == "sleeper_task" for t in snap1.running_tasks)
        assert any(t.name == "sleeper_task" for t in snap1.terminated_tasks)
        # Ensure snap0 was frozen and didn't change
        assert any(t.name == "sleeper_task" for t in snap0.running_tasks)
        assert not any(t.name == "sleeper_task" for t in snap0.terminated_tasks)

        mon.delete_snapshot(sid0)
        with pytest.raises(KeyError):
            mon.get_snapshot(sid0)
        with pytest.raises(KeyError):
            mon.delete_snapshot(sid0)

        summaries = mon.list_snapshots()
        assert len(summaries) == 1
        assert summaries[0].snapshot_id == 1
        assert summaries[0].name is None
        assert summaries[0].running_count == len(snap1.running_tasks)
        assert summaries[0].terminated_count == len(snap1.terminated_tasks)
        assert summaries[0].termination_count == len(snap1.terminated_tasks)


@pytest.mark.asyncio
async def test_max_snapshots(event_loop):
    with start_monitor(
        event_loop,
        console_enabled=False,
        hook_task_factory=True,
        max_snapshots=2,
    ) as mon:
        s0 = await mon.capture_snapshot("s0")
        s1 = await mon.capture_snapshot("s1")
        s2 = await mon.capture_snapshot("s2")
        assert (s0, s1, s2) == (0, 1, 2)
        with pytest.raises(KeyError):
            mon.get_snapshot(s0)
        assert [s.snapshot_id for s in mon.list_snapshots()] == [1, 2]
        diff = mon.diff_snapshots(s1, s2)
        assert diff.from_snapshot_id == 1
        assert diff.to_snapshot_id == 2
        with pytest.raises(KeyError):
            mon.diff_snapshots(s0, s2)
        with pytest.raises(KeyError):
            mon.format_running_task_stack(999999999)
        with pytest.raises(KeyError):
            mon.format_terminated_task_stack("nonexistent")


@pytest.mark.asyncio
async def test_snapshot_formatting_and_diff(event_loop):
    async def sleeper():
        await asyncio.sleep(100)

    async def short_task():
        await asyncio.sleep(0.01)

    with Monitor(event_loop, console_enabled=False, hook_task_factory=True) as mon:
        t1 = asyncio.create_task(sleeper(), name="task_one")
        t2 = asyncio.create_task(sleeper(), name="task_two")
        t3 = asyncio.create_task(short_task(), name="task_short")
        await t3
        await asyncio.sleep(0.05)

        s0 = await mon.capture_snapshot("snap0")

        running0 = mon.format_snapshot_task_list(s0, "", False)
        assert any(t.name == "task_one" for t in running0)
        assert any(t.name == "task_two" for t in running0)
        filtered_running0 = mon.format_snapshot_task_list(s0, "task_one", False)
        assert len(filtered_running0) == 1
        assert filtered_running0[0].name == "task_one"

        term0 = mon.format_snapshot_terminated_task_list(s0, "", False)
        assert any(t.name == "task_short" for t in term0)

        stack0 = mon.format_snapshot_task_stack(s0, id(t1))
        assert len(stack0) >= 2
        assert any(item.type == "header" for item in stack0)

        t1.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t1
        t4 = asyncio.create_task(sleeper(), name="task_four")
        await asyncio.sleep(0.05)

        s1 = await mon.capture_snapshot("snap1")

        # t1 is now terminated, but its stack in s0 should still be accessible
        stack0_after = mon.format_snapshot_task_stack(s0, id(t1))
        assert stack0_after == stack0

        with pytest.raises(KeyError):
            mon.format_snapshot_task_stack(s0, 999999999)

        diff = mon.format_snapshot_diff(s0, s1)
        assert any(t.name == "task_four" for t in diff.added)
        assert any(t.name == "task_one" for t in diff.removed)
        assert any(t.name == "task_two" for t in diff.common)

        t2.cancel()
        t4.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t2
            await t4


@pytest.mark.asyncio
async def test_snapshot_cli_commands(monitor: Monitor):
    async def sleeper():
        await asyncio.sleep(100)

    t1 = monitor._monitored_loop.create_task(sleeper(), name="cli_task_1")
    await asyncio.sleep(0.05)

    resp = await invoke_command(monitor, ["snapshot", "save", "--name", "first_snap"])
    assert "0" in resp
    assert "first_snap" in resp

    t2 = monitor._monitored_loop.create_task(sleeper(), name="cli_task_2")
    await asyncio.sleep(0.05)

    resp = await invoke_command(monitor, ["snapshot", "save"])
    assert "1" in resp

    resp = await invoke_command(monitor, ["snapshot", "list"])
    assert "first_snap" in resp
    assert "0" in resp
    assert "1" in resp

    resp = await invoke_command(monitor, ["snapshot", "ls"])
    assert "first_snap" in resp

    resp = await invoke_command(monitor, ["snapshot", "show", "0"])
    assert "cli_task_1" in resp
    assert "cli_task_2" not in resp

    resp = await invoke_command(monitor, ["snapshot", "where", "0", str(id(t1))])
    assert "Task" in resp

    resp = await invoke_command(monitor, ["snapshot", "where", "0", "999999999"])
    assert "No task 999999999" in resp

    resp = await invoke_command(monitor, ["snapshot", "diff", "0", "1"])
    assert "Added" in resp
    assert "Removed" in resp
    assert "Common" in resp
    assert "cli_task_2" in resp

    resp = await invoke_command(monitor, ["snapshot", "delete", "0"])
    assert "0" in resp

    for subcmd in (
        ["snapshot", "show", "999"],
        ["snapshot", "where", "999", str(id(t1))],
        ["snapshot", "diff", "999", "1"],
        ["snapshot", "diff", "1", "999"],
        ["snapshot", "delete", "999"],
    ):
        resp = await invoke_command(monitor, subcmd)
        assert "999" in resp

    t1.cancel()
    t2.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t1
        await t2
