"""The tools the agent calls, and the seam where your systems go.

Every scenario in `flows.json` names the tools it calls, with the arguments
they take and the result they returned when the scenario was recorded. That
recorded result is the stub: out of the box, calling a tool replays it, so the
whole conversation runs before you have connected anything.

Replacing one is the entire integration:

    from tools import implement

    @implement("book_appointment")
    def book_appointment(slot: str, patient_id: str) -> dict:
        row = his.create_appointment(slot=slot, patient=patient_id)
        return {"booking_id": row.reference, "sms_sent": True}

The contract is already written down — the recorded arguments and result are
the shape your function has to keep. Anything not yet implemented keeps
replaying the recording, and `unimplemented()` lists what is left, so a
half-finished integration is visible rather than silently fake.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

REGISTRY: dict[str, Callable[..., dict]] = {}

#: Tools that have been asked for at least once, whether implemented or not.
SEEN: set[str] = set()


def implement(name: str):
    """Register a real implementation for one tool."""

    def register(fn: Callable[..., dict]) -> Callable[..., dict]:
        REGISTRY[name] = fn
        return fn

    return register


def call(name: str, args: dict, *, recorded: dict | None = None) -> dict:
    """Run a tool, or replay what the scenario recorded.

    Errors are returned rather than raised. A tool that throws mid-call takes
    the conversation down with it; one that answers `{"error": ...}` lets the
    agent apologise and hand over to a person, which is what should happen.
    """
    SEEN.add(name)
    fn = REGISTRY.get(name)

    if fn is None:
        log.info("tool %s not implemented — replaying the recorded result", name)
        return dict(recorded or {}, stub=True)

    try:
        return fn(**args)
    except TypeError as exc:
        # The signature does not match what the scenario passes. Worth being
        # loud about: it means the contract drifted.
        log.error("tool %s rejected %s: %s", name, sorted(args), exc)
        return {"error": f"{name} does not accept {sorted(args)}"}
    except Exception as exc:  # noqa: BLE001
        log.exception("tool %s failed", name)
        return {"error": str(exc)}


def unimplemented(flows: dict) -> list[str]:
    """Every tool the scenarios call that still has no implementation."""
    named = {
        beat.get("name")
        for flow in flows.get("flows", [])
        for beat in flow.get("beats", [])
        if beat.get("kind") == "tool" and beat.get("name")
    }
    return sorted(name for name in named if name not in REGISTRY)


# ---------------------------------------------------------------------------
# Your implementations go below, or in a module you import here. Nothing above
# this line needs to change.
# ---------------------------------------------------------------------------
