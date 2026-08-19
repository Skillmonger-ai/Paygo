"""Example: a Paygo-aware agent that talks HTTP. Prefer ``paygo demo`` to try it.

    paygo exec -b 0.25 -- python examples/spend_agent.py

Each search costs $0.10. A $0.25 ceiling authorizes two purchases and denies
the third — the product promise: the agent can spend, and it cannot exceed $X.
"""

from __future__ import annotations

import sys

from paygo.demo_agent import main

if __name__ == "__main__":
    sys.exit(main())
