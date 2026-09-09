"""Lifecycle contracts for the Tk view without constructing a Tk root."""

import ast
from concurrent.futures import Future
from pathlib import Path

from lenkraster.studio.controller import JobToken, StudioController
from lenkraster.studio.view import StudioWindow


def test_pass_status_is_reserved_for_evidence_verdict_callbacks():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lenkraster"
        / "studio"
        / "view.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    callbacks = set()
    for function in (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_set_status"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "pass"):
                callbacks.add(function.name)

    assert callbacks <= {
        "_inspection_finished",
        "_motion_qa_finished",
        "_aseprite_qa_finished",
    }


class _PendingFuture:
    def __init__(self):
        self.cancel_calls = 0

    def done(self):
        return False

    def cancel(self):
        self.cancel_calls += 1


class _Root:
    def __init__(self):
        self.destroy_calls = 0

    def after(self, _delay, _callback):
        return "poll"

    def destroy(self):
        self.destroy_calls += 1


class _Text:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class _Executor:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self, **_kwargs):
        self.shutdown_calls += 1


class _RejectingExecutor:
    def submit(self, _work):
        raise RuntimeError("worker unavailable")


class _RejectingAfterRoot:
    def after(self, _delay, _callback):
        raise RuntimeError("Tk scheduler unavailable")


def test_close_waits_for_an_active_native_or_write_job():
    """Closing cannot imply cancellation while a bounded worker is still running."""
    window = object.__new__(StudioWindow)
    future = _PendingFuture()
    root = _Root()
    executor = _Executor()
    window.root = root
    window._executor = executor
    window._pending = (future, JobToken(0, 1), lambda _result: None, "failed")
    window._poll_after = "poll"
    window._play_after = None
    window._closed = False
    window._close_requested = False
    window.status_text = _Text()

    window.close()

    assert window._close_requested is True
    assert window._closed is False
    assert root.destroy_calls == 0
    assert executor.shutdown_calls == 0
    assert future.cancel_calls == 0
    assert "finishing" in window.status_text.value.lower()


def test_submit_failure_restores_idle_state_and_uses_fixed_error_text():
    window = object.__new__(StudioWindow)
    window.controller = StudioController()
    window._executor = _RejectingExecutor()
    window._closed = False
    window._pending = None
    window._poll_after = None
    window.status_text = _Text()
    window._sync_controls = lambda: None

    window._submit(
        "Working…",
        lambda: 1,
        lambda _result: None,
        "Task could not start.",
    )

    assert window.controller.busy is False
    assert window._pending is None
    assert "Task could not start." in window.status_text.value
    assert "worker unavailable" not in window.status_text.value


def test_poll_scheduling_failure_finishes_the_bounded_job_synchronously():
    completed = Future()
    completed.set_result({"ok": True})

    class _CompletedExecutor:
        def submit(self, _work):
            return completed

    window = object.__new__(StudioWindow)
    window.controller = StudioController()
    window._executor = _CompletedExecutor()
    window.root = _RejectingAfterRoot()
    window._closed = False
    window._close_requested = False
    window._pending = None
    window._poll_after = None
    window.status_text = _Text()
    window._sync_controls = lambda: None
    accepted = []

    window._submit(
        "Working…",
        lambda: {"ok": True},
        accepted.append,
        "Task failed.",
    )

    assert accepted == [{"ok": True}]
    assert window.controller.busy is False
    assert window.controller.result == {"ok": True}
    assert window._pending is None


def test_tk_callback_exception_handler_redacts_private_exception_text(capsys):
    window = object.__new__(StudioWindow)
    window._closed = False
    window.status_text = _Text()
    private_detail = RuntimeError(f"callback failed at {Path.home() / 'private.png'}")

    window._report_callback_exception(RuntimeError, private_detail, None)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""
    assert window.status_text.value.endswith("An unexpected interface error occurred.")
    assert str(Path.home()) not in window.status_text.value


def test_inspection_with_findings_reports_review_instead_of_pass():
    window = object.__new__(StudioWindow)
    window.status_text = _Text()
    window.inspect_canvas = object()
    window.inspect_preview_text = _Text()
    window.inspect_results = object()
    window._show_image = lambda *_args: None
    window._set_text = lambda *_args: None
    report = {
        "critique": {"findings": [{"check": "silhouette"}]},
        "contrast": {"weak": []},
    }

    window._inspection_finished("sprite.png", (report, object()))

    assert "STATUS: REVIEW" in window.status_text.value
    assert "STATUS: PASS" not in window.status_text.value


def test_escape_does_not_report_ready_while_a_job_is_active():
    window = object.__new__(StudioWindow)
    window.controller = StudioController()
    window.controller.begin_job()
    window.status_text = _Text()
    window._stop_motion_playback = lambda: None

    assert window._on_escape() == "break"

    assert "STATUS: WORKING" in window.status_text.value
    assert "READY" not in window.status_text.value
