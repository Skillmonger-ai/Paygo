"""The run supervisor: bring up the local service, hand back its URL, tear down.

Runs the FastAPI app via uvicorn in a background thread bound to 127.0.0.1 on an
ephemeral port. In-process (rather than a separate daemon) keeps the server's
lifetime exactly the lifetime of one ``paygo exec`` invocation.
"""

from __future__ import annotations

import threading
import time

import uvicorn
from fastapi import FastAPI

from paygo.credentials import scrub

# Env vars injected into every child. Harness adapters (M5/M7) extend this
# contract via the ``extra`` argument rather than forking process-launch code.
ENV_RUN_ID = "PAYGO_RUN_ID"
ENV_BASE_URL = "PAYGO_BASE_URL"
ENV_SESSION_TOKEN = "PAYGO_SESSION_TOKEN"
ENV_DEMO_MERCHANT = "PAYGO_DEMO_MERCHANT_URL"


def build_child_environment(
    parent: dict[str, str],
    *,
    run_id: str,
    base_url: str,
    token: str,
    strict: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment a child process is launched with.

    This is the composition hook: a harness adapter adds keys through ``extra``
    (e.g. ``OPENAI_BASE_URL`` pointing at Paygo). ``--strict`` scrubs known
    provider/wallet credentials from the parent first; ``extra`` is applied
    last so a harness can then set ``OPENAI_API_KEY`` to the Paygo session
    token without leaking the real upstream key.
    """
    env = dict(parent)
    if strict:
        env = scrub(env)
    env[ENV_RUN_ID] = run_id
    env[ENV_BASE_URL] = base_url
    env[ENV_SESSION_TOKEN] = token
    if extra:
        env.update(extra)
    return env


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
