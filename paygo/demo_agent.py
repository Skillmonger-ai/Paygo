"""Built-in demo agent: buy fake search until the budget is exhausted.

Invoked by ``paygo demo`` (or ``python -m paygo.demo_agent`` under ``paygo exec``).
Lives in the package so a PATH-installed ``paygo`` does not need the git clone.
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
        print(
            "Not running under Paygo (need PAYGO_BASE_URL, PAYGO_SESSION_TOKEN, "
            "PAYGO_DEMO_MERCHANT_URL)."
        )
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
                print(
                    f"agent → search {query!r}  DENIED (remaining {detail.get('remaining', '?')})"
                )
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
