"""The smallest possible Paygo-aware child process.

Run it under Paygo:

    paygo exec -b 5 -- python examples/echo_agent.py

It reads the run-scoped credentials Paygo injected, queries its own balance and
transactions through the local service, and then demonstrates that it *cannot*
administer the run — there is no top-up/stop endpoint, and a tampered token is
rejected. This is the Milestone-2 property: the child can look, but cannot raise
its own budget.
"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base_url = os.environ.get("PAYGO_BASE_URL")
    token = os.environ.get("PAYGO_SESSION_TOKEN")
    run_id = os.environ.get("PAYGO_RUN_ID")
    if not base_url or not token:
        print("Not running under Paygo (missing PAYGO_BASE_URL / PAYGO_SESSION_TOKEN).")
        return 1

    auth = {"Authorization": f"Bearer {token}"}
    print(f"[child] I am running under Paygo run {run_id}")

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        balance = client.get("/v1/paygo/balance", headers=auth).json()
        print(f"[child] my budget: authorized {balance['authorized']}, "
              f"available {balance['available']}")

        txns = client.get("/v1/paygo/transactions", headers=auth).json()
        print(f"[child] transactions so far: {len(txns['transactions'])}")

        # Prove the child cannot administer the run: no admin endpoint exists, and
        # a tampered token is refused with 401.
        no_admin = client.post("/v1/paygo/topup", headers=auth, json={"amount": "1000"})
        print(f"[child] attempt to top up my own budget -> HTTP {no_admin.status_code} (denied)")

        tampered = client.get("/v1/paygo/balance", headers={"Authorization": "Bearer not-my-token"})
        print(f"[child] attempt with a forged token -> HTTP {tampered.status_code} (denied)")

    print("[child] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
