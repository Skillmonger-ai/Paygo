"""A Paygo-aware agent that buys fake search until its budget is exhausted.

    paygo exec -b 0.25 -- python examples/spend_agent.py

Each search costs $0.10 (the demo merchant's exact price). A $0.25 ceiling
therefore authorizes two purchases and denies the third — the Milestone-3
definition of done: the agent can spend, and it cannot exceed $X.
"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    base_url = os.environ.get("PAYGO_BASE_URL")
    token = os.environ.get("PAYGO_SESSION_TOKEN")
    merchant = os.environ.get("PAYGO_DEMO_MERCHANT_URL")
    if not base_url or not token or not merchant:
        print("Not running under Paygo (need PAYGO_BASE_URL, PAYGO_SESSION_TOKEN, "
              "PAYGO_DEMO_MERCHANT_URL).")
        return 1

    auth = {"Authorization": f"Bearer {token}"}
    search_url = merchant.rstrip("/") + "/search"
    queries = ["composability", "hard ceilings", "one more than the budget allows"]

    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        for query in queries:
            resp = client.post(
                "/v1/paygo/request",
                headers=auth,
                json={"url": search_url, "method": "POST", "json": {"q": query}},
            )
            if resp.status_code == 403:
                detail = resp.json().get("detail") or {}
                print(f"agent → search {query!r}  DENIED "
                      f"(remaining {detail.get('remaining', '?')})")
                break
            if resp.status_code != 200:
                print(f"agent → search {query!r}  HTTP {resp.status_code}: {resp.text}")
                return 1
            body = resp.json()
            print(f"agent → search {query!r}  -{body['settled']}")

        balance = client.get("/v1/paygo/balance", headers=auth).json()
        print(
            f"\nAuthorized  {balance['authorized']}"
            f"\nSpent       {balance['settled']}"
            f"\nRemaining   {balance['available']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
