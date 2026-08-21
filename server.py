"""Flask + Twilio: the same agent, on a phone line.

Twilio calls this app once per turn. Each turn the caller's words go to the
agent, the agent's reply is synthesised with Voho, and Twilio is handed a URL
to play. Audio is held in memory against a random id and dropped once played
— a call is a handful of turns and none of it needs to outlive the call.

Point a Twilio number's Voice webhook at POST /voice.
"""

from __future__ import annotations

import os
import secrets
import threading
from typing import Dict, Tuple

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, url_for

load_dotenv()

import agent as agent_mod  # noqa: E402
import tools  # noqa: E402
import voho  # noqa: E402

app = Flask(__name__)
FLOWS = agent_mod.load_flows()

_calls: Dict[str, agent_mod.Agent] = {}
_clips: Dict[str, Tuple[bytes, str]] = {}
_lock = threading.Lock()

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")


def _clip(text: str) -> str:
    audio = voho.speak(text, fmt="mp3")
    clip_id = secrets.token_urlsafe(12)
    with _lock:
        _clips[clip_id] = (audio, "audio/mpeg")
    path = url_for("clip", clip_id=clip_id)
    return f"{PUBLIC_URL}{path}" if PUBLIC_URL else path


def _twiml(lines: list[str], *, listen: bool) -> Response:
    plays = "\n    ".join(f"<Play>{_clip(line)}</Play>" for line in lines if line)
    if listen:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech" language="ar-SA" speechTimeout="auto" action="/voice/turn" method="POST">
    {plays}
  </Gather>
  <Redirect method="POST">/voice/turn</Redirect>
</Response>"""
    else:
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  {plays}
  <Hangup/>
</Response>"""
    return Response(body, mimetype="text/xml")


def _spoken(turns: list[agent_mod.Turn]) -> list[str]:
    return [t.ar for t in turns if t.kind == "say" and t.ar]


@app.post("/voice")
def voice():
    call_sid = request.form.get("CallSid", secrets.token_urlsafe(8))
    convo = agent_mod.Agent(FLOWS)
    turns = convo.start(os.getenv("DEFAULT_FLOW") or None)
    with _lock:
        _calls[call_sid] = convo
    return _twiml(_spoken(turns), listen=not (convo.call and convo.call.done))


@app.post("/voice/turn")
def turn():
    call_sid = request.form.get("CallSid", "")
    said = request.form.get("SpeechResult", "").strip()

    with _lock:
        convo = _calls.get(call_sid)
    if convo is None:
        return _twiml(["معليش، صار عندنا خلل تقني. جرب تتصل مرة ثانية."], listen=False)
    if not said:
        return _twiml(["ما سمعتك زين. تقدر تعيد؟"], listen=True)

    turns = convo.reply(said)
    finished = bool(convo.call and convo.call.done)
    if finished:
        with _lock:
            _calls.pop(call_sid, None)
    return _twiml(_spoken(turns), listen=not finished)


@app.get("/clip/<clip_id>")
def clip(clip_id: str):
    with _lock:
        found = _clips.pop(clip_id, None)  # played once, then gone
    if not found:
        return Response(status=404)
    audio, content_type = found
    return Response(audio, mimetype=content_type)


@app.get("/flows")
def flows():
    """What this agent can hold a conversation about."""
    return jsonify(
        {
            "industry": FLOWS.get("industry"),
            "flows": [
                {"id": f["id"], "name": f["name"], "kind": f["kind"], "promise": f["promise"]}
                for f in FLOWS.get("flows", [])
            ],
            "tools_still_stubbed": tools.unimplemented(FLOWS),
        }
    )


@app.get("/health")
def health():
    return {"ok": True, "voice": voho.DEFAULT_VOICE, "model": voho.DEFAULT_MODEL}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
