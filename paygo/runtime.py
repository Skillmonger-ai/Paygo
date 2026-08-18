"""The run supervisor: bring up the local service, hand back its URL, tear down.

Runs the FastAPI app via uvicorn in a background thread bound to 127.0.0.1 on an
ephemeral port. In-process (rather than a separate daemon) keeps Milestone 2
small: the server's lifetime is exactly the lifetime of one ``paygo exec``
invocation, and teardown is deterministic.
"""

from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI


class LocalRuntime:
    """Serves a FastAPI app on 127.0.0.1:<ephemeral> for the life of a run."""

    def __init__(self, app: FastAPI) -> None:
        # port=0 lets the OS pick a free port, avoiding collisions when several
        # runs share a machine.
        self._config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning"
        )
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None
        self._base_url: str | None = None

    def start(self, timeout: float = 10.0) -> str:
        """Start the server thread and return its base URL once accepting."""
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        # Wait for uvicorn to bind and mark itself started before we read the
        # actual port; otherwise the socket list is not yet populated.
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Paygo local runtime failed to start in time.")
            time.sleep(0.01)

        port = self._server.servers[0].sockets[0].getsockname()[1]
        self._base_url = f"http://127.0.0.1:{port}"
        return self._base_url

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            raise RuntimeError("Runtime not started.")
        return self._base_url

    def stop(self, timeout: float = 10.0) -> None:
        """Signal uvicorn to exit and join the thread."""
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
