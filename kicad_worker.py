#!/usr/bin/env python3
"""
kicad_worker.py — MODULE (not launched on its own)

Owns the ONLY connection to KiCad of the whole program, and uses it ONLY from its own dedicated
internal thread. Every other point of the program (the main thread of the command-line interface,
a periodic timer, in the future an external GUI)
must NEVER call the kipy KiCad/Schematic object directly: it must go through here, by submitting
a function to be executed.

WHY IT IS NEEDED (verified in the source code, not assumed):
  kipy.client.KiCadClient opens a raw `pynng.Req0` socket (no `new_context()`), and its `send()`
  method has no internal lock around `self._conn.send()` / `self._conn.recv_msg()`. The pynng
  documentation explicitly says: "Without opening a Context, the socket can only have a
  single outstanding request at a time." Two threads calling the API concurrently, without
  EXTERNAL serialisation, risk a `pynng.BadState` exception or — worse — a reply read by the
  wrong thread. This module is the external serialisation: a single thread touches kipy, all the
  others send it messages through a queue (which is already thread-safe by itself, no lock to
  write by hand).

HOW TO USE IT
  worker = KiCadWorker()
  worker.start()                      # creates the connection INSIDE the thread
  ...
  risultato = worker.run(func, arg1, arg2)   # blocking, waits for the result
  worker.schedule_periodic(funzione_di_controllo, interval_sec=3)  # non-blocking
  ...
  worker.stop()                      # clean shutdown, always call it at the end of the program

IMPORTANT INVARIANT for whoever writes code that uses this worker:
  Any object that keeps "live" state tied to the schematic (e.g. the Session class of
  interactive_session.py: which components are already placed, which circles are
  active) must be read and written ONLY from INSIDE functions passed to
  `run()`/`schedule_periodic()` — never directly from another thread. As long as this rule is
  respected, that state needs no locks of its own: all mutations happen serially, always on the
  worker's own thread.
"""

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class WorkerStoppedError(RuntimeError):
    """Raised if the worker is used after stop(), or before start()."""


@dataclass
class _Command:
    """A single job to be executed inside the worker's thread.

    `reply` is a ONE-slot queue: the calling thread of `run()` waits on it with `.get()`,
    the worker puts the result in it (or the exception, re-raised afterwards in the calling
    thread) when it has finished.
    For periodic commands (`schedule_periodic`) nobody is waiting: `reply` stays `None` and
    errors are handled on the spot (see `_esegui_periodico_silenzioso` in `_main_loop`).
    """
    func: Callable[..., Any]
    args: tuple
    kwargs: dict
    reply: Optional["queue.Queue"] = None
    is_periodic: bool = False
    log_name: str = ""


def _is_busy_error(error: BaseException) -> bool:
    """KiCad answers "KiCad is busy and cannot respond to API requests right now" while a dialog or
    an interactive command (drawing a wire...) is open. Recognised by the text, so this module does
    not need to import kipy."""
    return type(error).__name__ == "ApiError" and "busy" in str(error).lower()


class KiCadWorker:
    def __init__(self):
        # Optional callback for run(): called with the "KiCad is busy" error, in the CALLING thread.
        # If it returns True the command is sent again, otherwise the error is raised as usual.
        self.on_busy: Optional[Callable[[BaseException], bool]] = None
        self._queue: "queue.Queue[_Command]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        # registered periodic commands: (function, interval_sec, next_execution)
        self._periodic_jobs: list[dict] = []

    # ------------------------------------------------------------------
    # Life cycle
    # ------------------------------------------------------------------

    def start(self):
        """Creates the dedicated thread. The actual connection to KiCad
        (kipy.KiCad()) is NOT opened here: the caller opens it with the first
        function it passes to run(), so this module does not depend on kipy
        (it stays a generic worker, reusable for other things too)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._main_loop, name="kicad-worker", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Clean shutdown: asks the thread to terminate and waits for it to do so.
        ALWAYS call it at the end of the program (also in case of error: use
        a try/finally block in the caller)."""
        if not self._running:
            return
        self._running = False
        # wake up the main loop if it is waiting on the queue
        self._queue.put(_Command(func=lambda: None, args=(), kwargs={}))
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Sending commands
    # ------------------------------------------------------------------

    def run(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """BLOCKING: executes `func(*args, **kwargs)` inside the worker's
        thread, waits for the result and returns it. If the function raises
        an exception, it is re-raised HERE, in the calling thread — so the
        code that calls run() handles it as if it had called the function
        directly, without needing to know that it runs on another thread."""
        if not self._running:
            raise WorkerStoppedError("The worker has not been started (call start() first)")
        while True:
            reply: "queue.Queue" = queue.Queue(maxsize=1)
            self._queue.put(_Command(func=func, args=args, kwargs=kwargs, reply=reply))
            ok, value = reply.get()
            if ok:
                return value
            if self.on_busy is not None and _is_busy_error(value) and self.on_busy(value):
                continue            # KiCad was busy and the caller wants another try
            raise value  # it is an exception, already ready to be re-raised

    def schedule_periodic(self, func: Callable[[], Any], interval_sec: float,
                          log_name: str = "", ignore_busy: bool = True):
        """NON-blocking: registers `func` to be called back (without
        arguments) every `interval_sec` seconds, inside the worker's thread,
        serialised together with all the other commands. Designed for the
        periodic recheck of the circle positions.

        `ignore_busy`: if True (default), a "KiCad is busy..." error is
        considered normal (the technician has an interactive operation in
        progress) and silenced — it is retried on the next round. Any OTHER
        error is printed in full (never swallowed without a trace).

        The actual registration (append to self._periodic_jobs) happens
        INSIDE the worker's thread, going through the queue as well: this way
        (a) self._periodic_jobs is mutated by a single thread, with no need for
        locks, and (b) if the worker was idle waiting with a long timeout
        (no periodic registered yet), the put() on the queue wakes it up
        immediately instead of waiting until the previous timeout."""
        def _register():
            self._periodic_jobs.append({
                "func": func,
                "interval_sec": interval_sec,
                "next_run": time.monotonic() + interval_sec,
                "log_name": log_name or func.__name__,
                "ignore_busy": ignore_busy,
            })
        self._queue.put(_Command(func=_register, args=(), kwargs={}))

    # ------------------------------------------------------------------
    # Internal loop (runs ONLY in the worker's thread)
    # ------------------------------------------------------------------

    def _main_loop(self):
        while self._running:
            # short timeout: allows checking the expired periodic commands too,
            # not only those that explicitly arrived in the queue
            timeout = self._time_to_next_periodic()
            try:
                command = self._queue.get(timeout=timeout)
            except queue.Empty:
                command = None

            if command is not None:
                self._run_command(command)

            self._run_due_periodic_jobs()

    def _time_to_next_periodic(self) -> float:
        if not self._periodic_jobs:
            return 1.0  # no periodic: check the queue every second anyway
        now = time.monotonic()
        upcoming = [p["next_run"] - now for p in self._periodic_jobs]
        return max(0.0, min(upcoming))

    def _run_command(self, command: _Command):
        try:
            value = command.func(*command.args, **command.kwargs)
            if command.reply is not None:
                command.reply.put((True, value))
        except Exception as e:  # noqa: BLE001 — must always be sent back to the caller or logged
            if command.reply is not None:
                command.reply.put((False, e))
            else:
                print(f"[kicad_worker] ERROR in a command with no expected reply: {e}")
                traceback.print_exc()

    def _run_due_periodic_jobs(self):
        now = time.monotonic()
        for p in self._periodic_jobs:
            if now < p["next_run"]:
                continue
            p["next_run"] = now + p["interval_sec"]
            try:
                p["func"]()
            except Exception as e:  # noqa: BLE001
                text = str(e)
                if p["ignore_busy"] and "busy" in text.lower():
                    # expected: the technician has an interactive operation in progress
                    # in KiCad. Retry on the next round, no log (so as not to fill the
                    # terminal with expected noise).
                    continue
                print(f"[kicad_worker] ERROR in the periodic job '{p['log_name']}': {e}")
                traceback.print_exc()
