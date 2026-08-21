"""Play a scenario in the terminal. No phone number, no API key needed to start.

    python examples/play.py                 # list the scenarios
    python examples/play.py card-dispute    # play one, speaking each line
    python examples/play.py card-dispute --silent

Every agent line is synthesised with Voho and written to out/, so you can hear
exactly what a caller would hear. --silent skips synthesis.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import agent as agent_mod  # noqa: E402
import tools  # noqa: E402
import voho  # noqa: E402

OUT = Path("out")
SILENT = "--silent" in sys.argv

DIM, GREEN, YELLOW, RESET = "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def show(turns: list[agent_mod.Turn], counter: list[int]) -> None:
    for turn in turns:
        if turn.kind == "tool":
            stub = " (stub)" if turn.result.get("stub") else ""
            print(f"\n  {YELLOW}tool{RESET}  {turn.tool}{DIM}{stub}{RESET}")
            print(f"        {DIM}{turn.args}{RESET}")
            print(f"        {DIM}→ {turn.result}{RESET}")
            continue

        print(f"\n  {GREEN}Voho{RESET}  {turn.ar}")
        if turn.en:
            print(f"        {DIM}{turn.en}{RESET}")
        if SILENT or not turn.ar:
            continue
        try:
            audio = voho.speak(turn.ar)
        except voho.VohoError as exc:
            print(f"        {DIM}(not synthesised: {exc}){RESET}")
            continue
        counter[0] += 1
        OUT.mkdir(exist_ok=True)
        path = OUT / f"line-{counter[0]:02d}.mp3"
        path.write_bytes(audio)
        print(f"        {DIM}{path} · {len(audio) // 1024} KB · voice {voho.DEFAULT_VOICE}{RESET}")


def main() -> None:
    data = agent_mod.load_flows()
    convo = agent_mod.Agent(data)
    wanted = next((a for a in sys.argv[1:] if not a.startswith("--")), None)

    if not wanted:
        print(f"\n  {data['industry']} — {len(data['flows'])} scenarios\n")
        for flow in data["flows"]:
            playable = "" if flow["kind"] == "call" else f"  {DIM}({flow['kind']}, reference only){RESET}"
            print(f"  {flow['id']:<26} {flow['name']}{playable}")
            print(f"  {DIM}{'':<26} {flow['promise']}{RESET}")
        missing = tools.unimplemented(data)
        if missing:
            print(f"\n  {DIM}Tools still replaying recorded results: {', '.join(missing)}{RESET}")
        print(f"\n  Play one:  python examples/play.py {data['flows'][0]['id']}\n")
        return

    counter = [0]
    turns = convo.start(wanted)
    show(turns, counter)

    while convo.call and not convo.call.done:
        try:
            said = input(f"\n  Caller  ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not said:
            # Enter on its own answers with what the scenario expects, so the
            # whole thing can be watched end to end without typing Arabic.
            beat = convo.call._next_caller_beat()
            if beat is None:
                break
            said = beat.get("ar", "")
            print(f"  Caller  {said}")
        show(convo.reply(said), counter)

    if convo.call:
        print(f"\n  {DIM}Outcome{RESET}")
        for label, value in convo.call.outcome():
            print(f"  {DIM}{label:<22}{RESET}{value}")
    print()


if __name__ == "__main__":
    main()
