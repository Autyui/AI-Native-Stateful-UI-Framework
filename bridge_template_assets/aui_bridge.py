#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib import error, request

try:
    from pywinpty import PtyProcess  # type: ignore
except Exception:
    try:
        from winpty import PtyProcess  # type: ignore
    except Exception:
        PtyProcess = None  # type: ignore


EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exit_reason(exit_code: int) -> str:
    try:
        code = int(exit_code)
    except Exception:
        return "unknown"
    if code == 0:
        return "completed"
    if code < 0:
        return f"terminated_by_signal_{abs(code)}"
    return "nonzero_exit"


def _emit_event(hook_url: str, payload: Dict[str, Any]) -> None:
    if not hook_url:
        return
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        hook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=2):
            pass
    except (error.URLError, TimeoutError):
        pass


def _append_log_event(log_path: Path, payload: Dict[str, Any]) -> None:
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _log_and_emit(log_path: Path, hook_url: str, payload: Dict[str, Any]) -> None:
    _append_log_event(log_path, payload)
    _emit_event(hook_url, payload)


def _parse_overrides(values: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in values:
        text = (raw or "").strip()
        if not text or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip().replace("-", "_")
        value = value.strip()
        low = value.lower()
        if low in {"true", "false"}:
            out[key] = low == "true"
        elif value.isdigit():
            out[key] = int(value)
        else:
            out[key] = value
    return out


def _to_flag_args(params: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key, value in params.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                out.append(flag)
            continue
        if value is None:
            continue
        out.extend([flag, str(value)])
    return out


def _resolve_python_interpreter(project_root: Path, config: Dict[str, Any]) -> str:
    explicit = str(config.get("python_interpreter") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p.resolve())

    venv_dir = str(config.get("venv_dir") or "").strip()
    search_dirs: List[str] = []
    if venv_dir:
        search_dirs.append(venv_dir)
    search_dirs.extend([".venv", "venv", "env", "web"])

    for name in search_dirs:
        if not name:
            continue
        win = project_root / name / "Scripts" / "python.exe"
        if win.exists():
            return str(win.resolve())
        posix = project_root / name / "bin" / "python"
        if posix.exists():
            return str(posix.resolve())
    return ""


def _resolve_project_root(bridge_dir: Path, config: Dict[str, Any]) -> Path:
    raw = str(config.get("project_root") or "").strip()
    candidates: List[Path] = []

    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = (bridge_dir / p).resolve()
        else:
            p = p.resolve()
        candidates.append(p)

    candidates.append((bridge_dir.parent.parent).resolve())

    hint_abs = str(config.get("project_root_hint_abs") or "").strip()
    if hint_abs:
        try:
            candidates.append(Path(hint_abs).resolve())
        except Exception:
            pass

    seen: set[str] = set()
    uniq: List[Path] = []
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    for c in uniq:
        if c.exists():
            return c
    return uniq[0] if uniq else bridge_dir.resolve()


def _resolve_command_base(command_base: List[str], project_root: Path, config: Dict[str, Any]) -> List[str]:
    if not command_base:
        return []
    out = list(command_base)
    first = out[0].strip().lower()
    if first in {"python", "python3", "py"}:
        resolved_python = _resolve_python_interpreter(project_root, config)
        if resolved_python:
            out[0] = resolved_python
        if len(out) <= 1 or str(out[1]).strip().lower() not in {"-u", "-ub"}:
            out.insert(1, "-u")
    return out


def _build_safe_env() -> Dict[str, str]:
    """
    Build a clean child process environment without shell string injection.
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "dumb"
    env["PROMPT"] = ""
    return env


class ConPTYSession:
    def __init__(self, *, cmd: List[str], cwd: Path, emit: EmitFn):
        self.cmd = cmd
        self.cwd = cwd
        self.emit = emit
        self.pty: Any = None
        self.stdin_q: asyncio.Queue[str] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task[Any]] = []
        self.exit_code: int = 1
        self.rows: int = 36
        self.cols: int = 140

    async def start(self) -> bool:
        if PtyProcess is None:
            print("!!! PTY startup failed: pywinpty/winpty is not installed.")
            await self.emit({"type": "error", "message": "pywinpty/winpty is not installed."})
            return False

        env = _build_safe_env()
        full_cmd = subprocess.list2cmdline(self.cmd)
        print(f"[ConPTY] spawning cmd={full_cmd} cwd={self.cwd}")
        try:
            self.pty = await asyncio.to_thread(
                PtyProcess.spawn,
                full_cmd,
                cwd=str(self.cwd),
                dimensions=(self.rows, self.cols),
                env=env,
            )
        except Exception as e:
            print(f"!!! PTY startup failed hard: {e}")
            print(traceback.format_exc())
            await self.emit(
                {
                    "type": "error",
                    "message": f"Spawn failed: {e}",
                    "command": self.cmd,
                    "cwd": str(self.cwd),
                }
            )
            return False

        echo_disabled = await asyncio.to_thread(self._try_disable_echo)

        await self.emit(
            {
                "type": "lifecycle",
                "event": "starting",
                "ts": _now_iso(),
                "command": self.cmd,
                "project_root": str(self.cwd),
                "echo_disabled": bool(echo_disabled),
            }
        )

        self._tasks = [
            asyncio.create_task(self._read_loop(), name="pty-read"),
            asyncio.create_task(self._write_loop(), name="pty-write"),
            asyncio.create_task(self._watch_exit_loop(), name="pty-watch"),
        ]
        return True

    async def send_stdin(self, text: str) -> None:
        if self._stop.is_set():
            return
        await self.stdin_q.put(str(text or ""))

    async def resize(self, *, cols: int, rows: int) -> bool:
        if self._stop.is_set() or not self.pty:
            return False
        ncols = max(20, min(500, int(cols)))
        nrows = max(5, min(200, int(rows)))
        ok = await asyncio.to_thread(self._pty_resize, ncols, nrows)
        if ok:
            self.cols = ncols
            self.rows = nrows
            await self.emit(
                {
                    "type": "lifecycle",
                    "event": "resized",
                    "ts": _now_iso(),
                    "cols": ncols,
                    "rows": nrows,
                }
            )
        return bool(ok)

    async def wait(self) -> int:
        watcher = next((t for t in self._tasks if t.get_name() == "pty-watch"), None)
        if watcher is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        return int(self.exit_code)

    async def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()

        if self.pty:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.pty.terminate)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.pty.close)

        current = asyncio.current_task()
        pending: List[asyncio.Task[Any]] = []
        for t in self._tasks:
            if t is current:
                continue
            if not t.done():
                t.cancel()
            pending.append(t)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _read_loop(self) -> None:
        while not self._stop.is_set() and self.pty:
            try:
                chunk = await asyncio.to_thread(self.pty.read, 8192)
                if not chunk:
                    break
                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
                await self.emit(
                    {
                        "type": "raw",
                        "ts": _now_iso(),
                        "data": text,
                    }
                )
            except EOFError:
                break
            except Exception as e:
                if not self._stop.is_set():
                    await self.emit({"type": "error", "message": f"Read error: {e}"})
                break

    async def _write_loop(self) -> None:
        while not self._stop.is_set() and self.pty:
            try:
                payload = await asyncio.wait_for(self.stdin_q.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            try:
                await asyncio.to_thread(self.pty.write, payload)
            except Exception as e:
                if not self._stop.is_set():
                    await self.emit({"type": "error", "message": f"Write error: {e}"})
                break

    async def _watch_exit_loop(self) -> None:
        while not self._stop.is_set() and self.pty:
            try:
                alive = await asyncio.to_thread(self._pty_is_alive)
            except Exception:
                alive = False
            if not alive:
                break
            await asyncio.sleep(0.2)

        self.exit_code = int(self._pty_exit_code())
        await self.emit(
            {
                "type": "lifecycle",
                "event": "exited",
                "ts": _now_iso(),
                "exit_code": int(self.exit_code),
                "reason": _exit_reason(int(self.exit_code)),
            }
        )
        self._stop.set()

    def _pty_is_alive(self) -> bool:
        if not self.pty:
            return False
        checker = getattr(self.pty, "isalive", None)
        if callable(checker):
            return bool(checker())
        return False

    def _pty_exit_code(self) -> int:
        if not self.pty:
            return 1
        for key in ("exitstatus", "exit_code", "status"):
            val = getattr(self.pty, key, None)
            if val is None:
                continue
            try:
                return int(val)
            except Exception:
                continue
        return 1

    def _pty_resize(self, cols: int, rows: int) -> bool:
        if not self.pty:
            return False
        methods = [
            ("setwinsize", (rows, cols)),
            ("set_size", (cols, rows)),
            ("set_size", (rows, cols)),
            ("resize", (cols, rows)),
            ("resize", (rows, cols)),
        ]
        for name, args in methods:
            fn = getattr(self.pty, name, None)
            if not callable(fn):
                continue
            with contextlib.suppress(Exception):
                fn(*args)
                return True
        return False

    def _try_disable_echo(self) -> bool:
        if not self.pty:
            return False
        # Best-effort: some winpty/pty wrappers expose echo controls.
        probes = [
            ("setecho", (False,)),
            ("set_echo", (False,)),
            ("setecho", (0,)),
            ("set_echo", (0,)),
            ("echo", (False,)),
        ]
        for name, args in probes:
            fn = getattr(self.pty, name, None)
            if not callable(fn):
                continue
            with contextlib.suppress(Exception):
                fn(*args)
                return True
        return False


def _stdin_forwarder_thread(
    *,
    loop: asyncio.AbstractEventLoop,
    session: ConPTYSession,
    log_path: Path,
    hook_url: str,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        line = sys.stdin.readline()
        if line == "":
            break
        text = line.rstrip("\r\n")
        if not text:
            continue
        try:
            fut = asyncio.run_coroutine_threadsafe(session.send_stdin(text + "\n"), loop)
            fut.result(timeout=3)
            _log_and_emit(
                log_path,
                hook_url,
                {"ts": _now_iso(), "stream": "user_input", "line": text},
            )
        except Exception as e:
            _log_and_emit(
                log_path,
                hook_url,
                {"ts": _now_iso(), "stream": "bridge_error", "line": f"Failed to forward interactive stdin: {e}"},
            )
            break


async def _async_main() -> int:
    parser = argparse.ArgumentParser(description="AIUI bridge launcher")
    parser.add_argument("--set", action="append", default=[], help="override launch flags, format key=value")
    parser.add_argument("--stdin-text", default="", help="optional stdin text sent after process start")
    parser.add_argument("--hook-url", default="", help="optional dashboard callback URL")
    parser.add_argument("--print-command", action="store_true", help="print final command and exit")
    args, passthrough = parser.parse_known_args()

    bridge_dir = Path(__file__).resolve().parent
    log_path = bridge_dir / "bridge_runtime.log.jsonl"
    config_path = bridge_dir / "aui_bridge_config.json"
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        print(msg, file=sys.stderr)
        _append_log_event(log_path, {"ts": _now_iso(), "stream": "bridge_error", "line": msg})
        return 2

    config = json.loads(config_path.read_text(encoding="utf-8"))
    command_base = list(config.get("command_base") or [])
    if not command_base:
        msg = "Invalid config: command_base is empty"
        print(msg, file=sys.stderr)
        _append_log_event(log_path, {"ts": _now_iso(), "stream": "bridge_error", "line": msg})
        return 2

    project_root = _resolve_project_root(bridge_dir, config)
    command_base = _resolve_command_base(command_base, project_root, config)
    default_params = dict(config.get("flag_defaults") or {})
    overrides = _parse_overrides(args.set)
    merged_params = {**default_params, **overrides}
    cmd = command_base + _to_flag_args(merged_params) + list(config.get("passthrough_args") or []) + passthrough

    if args.print_command:
        print(subprocess.list2cmdline(cmd))
        return 0

    hook_url = args.hook_url or str(config.get("dashboard_hook_url") or "")

    async def emit(payload: Dict[str, Any]) -> None:
        evt = dict(payload or {})
        evt.setdefault("ts", _now_iso())
        _append_log_event(log_path, evt)
        if hook_url:
            await asyncio.to_thread(_emit_event, hook_url, evt)

    session = ConPTYSession(cmd=cmd, cwd=project_root, emit=emit)
    started = await session.start()
    if not started:
        await emit(
            {"type": "lifecycle", "event": "exited", "exit_code": 1, "reason": "launch_failed"}
        )
        return 1

    stdin_text = (args.stdin_text or "").strip()
    if stdin_text:
        await session.send_stdin(stdin_text)
        await emit({"stream": "user_input", "line": stdin_text})

    loop = asyncio.get_running_loop()
    stdin_stop = threading.Event()
    stdin_thread = threading.Thread(
        target=_stdin_forwarder_thread,
        kwargs={
            "loop": loop,
            "session": session,
            "log_path": log_path,
            "hook_url": hook_url,
            "stop_event": stdin_stop,
        },
        daemon=True,
    )
    stdin_thread.start()

    exit_code = await session.wait()
    stdin_stop.set()
    await session.stop()
    return int(exit_code)


def main() -> int:
    try:
        return int(asyncio.run(_async_main()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

