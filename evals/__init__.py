"""Prompt-contract and golden evals for the concert-intelligence agent (RC1-258).

Same two-layer split as `n8n-stakeholder-status-email`: layer 1 is free and
gates, layer 2 is billed and run by hand. The harness is
[`agent-evals`](https://github.com/snacksnack/agent-evals), pinned by tag.

The extraction layer is *not* shared with the sibling repo — the two workflows
assemble their prompts differently, and a premature abstraction over two
implementations would be the mistake ADR-0030 warned about in the planner. What
is shared is the harness, which is the part that was worth extracting.
"""

from __future__ import annotations

__version__ = "0.1.0"
