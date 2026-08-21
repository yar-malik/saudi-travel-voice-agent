"""The conversation engine.

A scenario in `flows.json` is a sequence of beats: things the caller says,
things the agent says, and tools called in between. This walks one — speaking
the agent's lines, calling the tools, and waiting at each caller beat for
something to come back from the phone.

Scripted rather than generative, and deliberately so. In a regulated sector
the first version has to say exactly what compliance signed off on. The seam
for a model is in `match()`: swap the matcher for an intent classifier and
the rest of the machinery is unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import tools

FLOWS_PATH = Path(__file__).with_name("flows.json")

TASHKEEL = re.compile(r"[ً-ْٰـ]")


def normalise(text: str) -> str:
    """Arabic written two ways is still one word."""
    text = TASHKEEL.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    return text.replace("ة", "ه").replace("ى", "ي").lower().strip()


def load_flows(path: Path = FLOWS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Turn:
    """One thing to say, or one thing that happened."""

    kind: str  # say | tool
    ar: str = ""
    en: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)


class Call:
    """One caller, working through one scenario."""

    def __init__(self, flow: dict, greeting: dict | None = None) -> None:
        self.flow = flow
        self.greeting = greeting or {}
        self.beats = flow.get("beats", [])
        self.at = 0
        self.done = False
        self.log: list[Turn] = []

    # ------------------------------------------------------------- driving

    def open(self) -> list[Turn]:
        """Everything the agent does before the caller has to speak.

        Most scenarios were recorded from the caller's first sentence, because
        that is where the interesting part starts. A real inbound call cannot
        open in silence, so the greeting goes first whenever the scenario
        itself does not begin with the agent.
        """
        turns: list[Turn] = []
        first = self.beats[0] if self.beats else None
        starts_with_caller = bool(
            first and first.get("kind") == "msg" and first.get("who") == "CALLER"
        )
        if starts_with_caller and self.greeting.get("ar"):
            turns.append(
                Turn(kind="say", ar=self.greeting["ar"], en=self.greeting.get("en", ""))
            )
            self.log.extend(turns)
        return turns + self._advance()

    def reply(self, said: str) -> list[Turn]:
        """The caller said something. Move on, and say what comes next."""
        if self.done:
            return []

        expected = self._next_caller_beat()
        if expected is not None and not self.match(said, expected):
            return [
                Turn(
                    kind="say",
                    ar="ممكن توضح لي أكثر؟",
                    en="Could you say a bit more?",
                )
            ]

        if expected is not None:
            self.at = self.beats.index(expected) + 1
        return self._advance()

    def _next_caller_beat(self) -> dict | None:
        for beat in self.beats[self.at:]:
            if beat.get("kind") == "msg" and beat.get("who") == "CALLER":
                return beat
        return None

    def _advance(self) -> list[Turn]:
        """Play forward until the caller has to speak again."""
        out: list[Turn] = []
        while self.at < len(self.beats):
            beat = self.beats[self.at]

            if beat.get("kind") == "msg" and beat.get("who") == "CALLER":
                break

            if beat.get("kind") == "tool":
                turn = self._run_tool(beat)
            else:
                turn = Turn(kind="say", ar=beat.get("ar", ""), en=beat.get("en", ""))

            out.append(turn)
            self.log.append(turn)
            self.at += 1

        if self.at >= len(self.beats):
            self.done = True
        return out

    def _run_tool(self, beat: dict) -> Turn:
        name = beat.get("name", "")
        args = _loads(beat.get("args"))
        recorded = _loads(beat.get("result"))
        result = tools.call(name, args, recorded=recorded)
        return Turn(kind="tool", tool=name, args=args, result=result)

    # ------------------------------------------------------------ matching

    def match(self, said: str, beat: dict) -> bool:
        """Is what the caller said close enough to what the script expects?

        Generous on purpose. A caller answering "الأربعاء زين" where the script
        expected "الأربعاء" has answered the question, and a matcher strict
        enough to reject that will reject most real callers. Anything that
        must not be got wrong — an amount, a date, a confirmation — belongs in
        a tool call that checks it properly, not in this comparison.
        """
        said_n = normalise(said)
        if not said_n:
            return False

        expected = normalise(beat.get("ar", ""))
        words = [w for w in re.split(r"\W+", expected) if len(w) > 2]
        if not words:
            return True

        hits = sum(1 for w in words if w in said_n)
        return hits >= max(1, len(words) // 4)

    # -------------------------------------------------------------- output

    def outcome(self) -> list[tuple[str, str]]:
        return [tuple(pair) for pair in self.flow.get("outcome", [])]


def _loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


class Agent:
    """Picks the scenario a caller is asking for, then runs it."""

    def __init__(self, flows: dict | None = None) -> None:
        self.data = flows or load_flows()
        self.industry = self.data.get("industry", "")
        self.greeting = self.data.get("greeting", {})
        self.flows = self.data.get("flows", [])
        self.call: Call | None = None

    @property
    def callable_flows(self) -> list[dict]:
        """The ones this engine can hold a conversation for."""
        return [f for f in self.flows if f.get("kind") == "call"]

    def choose(self, said: str) -> dict | None:
        """Route an opening line to a scenario, by its name and promise."""
        said_n = normalise(said)
        best, best_score = None, 0
        for flow in self.callable_flows:
            words = [
                w
                for w in re.split(r"\W+", normalise(f"{flow['name']} {flow['promise']}"))
                if len(w) > 3
            ]
            score = sum(1 for w in words if w in said_n)
            if score > best_score:
                best, best_score = flow, score
        return best or (self.callable_flows[0] if self.callable_flows else None)

    def start(self, flow_id: str | None = None) -> list[Turn]:
        flow = next(
            (f for f in self.callable_flows if f["id"] == flow_id),
            self.callable_flows[0] if self.callable_flows else None,
        )
        if flow is None:
            raise ValueError("flows.json has no conversational scenarios")
        self.call = Call(flow, greeting=self.greeting)
        return self.call.open()

    def reply(self, said: str) -> list[Turn]:
        if self.call is None:
            self.start()
        return self.call.reply(said) if self.call else []
