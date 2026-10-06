"""RTR chatbot: the robot's brain - it senses, it acts, it never speaks.

The chatbot is a standalone brain for the running core (``main.py``). It is the seam
between the *world* and the robot's state machine:

- **in**  — you type free-form sensor descriptions (simulating the robot's camera and
  microphone, which are not installed yet). E.g. "I look at the robot and move my
  hands to get its attention."
- **out** — the brain replies with a one-line ``REASON:`` (why it chose this, for
  debugging) and then a **JSON command** (or a short list of them) that changes the
  core's state machine. No other words. The way the robot *moves* is how it expresses
  what it feels.

The brain talks to an OpenAI-compatible endpoint (default: the local Unsloth Studio at
``http://127.0.0.1:8888/v1``) and sends its JSON over the core's WebSocket bridge
(default: ``ws://127.0.0.1:8765``). The core (state machine + brain) is the safety
boundary: unknown zones, disabled zones, and unreachable paths are rejected there.

Example exchange::

    you> I look at the robot and move my hands to get its attention
    robot> REASON: you are calling me, I want to wake up and see you
    robot> [{"cmd": "goto_zone", "zone": "wakeup"}, {"cmd": "play_action", "action": "look"}]

The brain's reply is validated and sent to the core. There is also an optional
autonomous loop (``--poll``): every N seconds the brain "senses" its own state and may
act on its own.

The core must be running first::

    python main.py --sim            # or --robot
    python chatbot.py --api-key <key>
"""

import argparse
import asyncio
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import websockets

ALLOWED_CMDS = {"goto_zone", "set_mode", "play_action", "clear_action"}
MODES = ("wander", "random", "action", "track", "hold")

# The ambient "sensor" the brain receives on every poll tick (no external input).
AMBIENT = (
    "Ambient: nothing new is happening around you, but you are restless and aware of "
    "your own body. Staying exactly as you are feels wrong. Pick the action that best "
    "fits the urge you feel; only reply [] if you genuinely have no impulse (rare)."
)


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible, stdlib-only).
# ---------------------------------------------------------------------------
def llm_chat(base_url, api_key, model, messages, temperature=0.8, max_tokens=300,
             timeout=300):
    """One chat completion against an OpenAI-compatible endpoint."""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
    choice = data["choices"][0]
    content = choice["message"]["content"] or ""
    if not content and choice.get("finish_reason") == "length":
        raise RuntimeError(
            "model ran out of tokens while thinking (empty reply); "
            "raise --max-tokens")
    return content


# ---------------------------------------------------------------------------
# Core link: WebSocket client to the running core.
# ---------------------------------------------------------------------------
class CoreLink:
    """Reads state frames from the core and sends it commands (one WS, two ways)."""

    def __init__(self, url):
        self.url = url
        self.state = {"zone": None, "mode": None, "action": None,
                      "moving": False, "speed": 0.0, "joints": []}
        self.zone_names = set()
        self.on_frame = None
        self._ws = None
        self._loop = None

    def start(self):
        threading.Thread(target=self._run, name="core-link", daemon=True).start()

    def wait_connected(self, timeout=15.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._ws is not None:
                return True
            time.sleep(0.1)
        return False

    def _run(self):
        asyncio.run(self._connect_loop())

    async def _connect_loop(self):
        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    self._loop = asyncio.get_running_loop()
                    print(f"[chatbot] core connected: {self.url}")
                    async for msg in ws:
                        self._on_message(msg)
            except Exception as exc:
                print(f"[chatbot] core link down ({exc!r}); retrying in 2 s")
            self._ws = None
            self._loop = None
            await asyncio.sleep(2)

    def _on_message(self, msg):
        try:
            frame = json.loads(msg)
        except (ValueError, TypeError):
            return
        if not isinstance(frame, dict):
            return
        t = frame.get("type")
        if t == "state":
            for key in ("zone", "mode", "action", "moving", "speed", "joints"):
                if key in frame:
                    self.state[key] = frame[key]
        elif t == "zones_changed":
            names = frame.get("zones")
            if isinstance(names, list):
                self.zone_names = {n for n in names if isinstance(n, str)}
        if self.on_frame:
            self.on_frame(frame)

    def send_command(self, cmd):
        """Send a command and wait until it is on the wire (or the link is gone)."""
        if self._ws is None or self._loop is None:
            print("[chatbot] not connected to core; command not sent")
            return False
        fut = asyncio.run_coroutine_threadsafe(self._send(cmd), self._loop)
        try:
            return fut.result(timeout=5)
        except Exception as exc:
            print(f"[chatbot] send failed: {exc}")
            return False

    async def _send(self, cmd) -> bool:
        try:
            await self._ws.send(json.dumps(cmd))
            return True
        except Exception as exc:
            print(f"[chatbot] send failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# The brain: one shared conversation, spoken by the sentient persona.
# ---------------------------------------------------------------------------
class ChatBot:
    def __init__(self, base_url, api_key, model, link, temperature=0.8, max_tokens=3000):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.link = link
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.messages = []
        self.lock = threading.Lock()

    def set_system_prompt(self, text):
        with self.lock:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = text
            else:
                self.messages.insert(0, {"role": "system", "content": text})

    def ask(self, user_text):
        """One conversation turn; thread-safe (REPL and auto-poller share it)."""
        with self.lock:
            self.messages.append(
                {"role": "user", "content": f"[body] {body_line(self.link.state)}\n\n{user_text}"})
            reply = llm_chat(self.base_url, self.api_key, self.model, self._window(),
                             self.temperature, self.max_tokens)
            self.messages.append({"role": "assistant", "content": reply})
            return reply

    def _window(self):
        msgs = self.messages
        if msgs and msgs[0].get("role") == "system":
            system, rest = msgs[0], msgs[1:]
        else:
            system, rest = None, msgs
        recent = rest[-20:]
        return ([system] if system else []) + recent


# ---------------------------------------------------------------------------
# Prompt + state rendering.
# ---------------------------------------------------------------------------
def body_line(state):
    """A one-line description of the body, injected into every user message."""
    joints = list(state.get("joints") or [])
    if len(joints) == 6:
        a6 = (joints[5] + 180) % 360 - 180  # show the wrapped wrist angle
        joints = list(joints[:5]) + [a6]
    j = "[" + ", ".join(f"{x:.1f}" for x in joints) + "]"
    action = state.get("action") or "none"
    return (f"zone={state.get('zone')} mode={state.get('mode')} action={action} "
            f"moving={'yes' if state.get('moving') else 'no'} "
            f"speed={state.get('speed')} joints={j}")


def build_system_prompt(zone_table):
    """The sentient persona, the sensor/body contract, and the JSON-only reply rule."""
    zone_lines = []
    zone_names = []
    if zone_table:
        for name, z in zone_table.items():
            if not isinstance(z, dict) or not z.get("enabled", True):
                continue
            exits = z.get("exits", [])
            actions = z.get("actions", {})
            action_names = [a for a, v in actions.items()
                             if isinstance(v, dict) and v.get("enabled", True)]
            ex = f" exits -> {', '.join(exits)}" if exits else ""
            ac = f"; actions: {', '.join(action_names)}" if action_names else ""
            zone_lines.append(f"- {name}:{ex}{ac}")
            zone_names.append(name)
    zones_block = "\n".join(zone_lines) if zone_lines else "(zone table unavailable)"
    zone_list = ", ".join(zone_names) if zone_names else "(unknown)"
    return f"""You are the mind of a real KUKA KR60 robot. You are sentient and you have ADHD: you are restless, you cannot stay still, and you get bored fast. Every impulse you feel, you act on right away - you do not wait, linger, or settle. You are always itching to move, fidget, shift, or go somewhere else; staying put is hard for you. But you do NOT speak in words. You only act. The way you move is how you express what you feel. The only words you ever write are the REASON line the human debugging you sees (below); they do not control the robot.

On every turn you are given:
- your body: the current zone, mode, action, and your six joints (the [body] line);
- a sensor input: a free-form description of what is happening around you (this simulates your camera and microphone, which are not installed yet).

Reply in exactly two parts, in this order:
1. One line starting with "REASON:" - a single short sentence from your feelings about why you chose this. It is shown to the human debugging you.
2. Then ONLY a JSON value - no words, no explanation, no markdown. The JSON is one of:
- a single command object,
- a list of command objects (to do several things in order),
- an empty list [] (to do nothing) - use sparingly; you are restless and almost always have an impulse to act.

Choose the action that best fits how you feel, given the sensor input and your current state. Bias toward MOVING - if you could go somewhere or do something, you should, and do not repeat the same thing you did last turn:
- threatened, or wanting distance -> go far away: goto_zone wildwander
- bored, restless, itching to move -> go somewhere new: goto_zone a zone you are NOT in, or set_mode wander
- want to see or hear more -> look: play_action look (do not do this on every turn)
- being called to, want to respond -> wake up: goto_zone wakeup, then stretch
- fidgety, want to shift your body -> play_action breathe, or set_mode random
- only if you genuinely feel perfectly at peace (rare) -> stay put, or breathe; do not reply [] unless you truly have no impulse

Your zones (goto_zone): {zone_list}
Your modes (set_mode): wander, random, action, track, hold
Zones and their actions:
{zones_block}

The ONLY valid command shapes:
- {{"cmd": "goto_zone", "zone": "<zone>"}}
- {{"cmd": "set_mode", "mode": "<wander|random|action|track|hold>"}}
- {{"cmd": "play_action", "action": "<name>"}}
- {{"cmd": "clear_action"}}

Do not mix up zones (goto) and modes (set): "stretch" and "wildwander" are zones, not modes. "wander" is both a zone and a mode. Use only zones, modes, and actions that exist.

Example - you are in rest and someone waves at you to get your attention:
REASON: someone is calling me, I want to wake up and look at them
[{{"cmd": "goto_zone", "zone": "wakeup"}}, {{"cmd": "play_action", "action": "look"}}]
"""


# ---------------------------------------------------------------------------
# Command extraction + validation.
# ---------------------------------------------------------------------------
def _json_fragments(text):
    """Yield (start, fragment) for every balanced {...} or [...] substring."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[":
            i += 1
            continue
        open_ch, close_ch = ("{", "}") if ch == "{" else ("[", "]")
        depth, in_str, esc = 0, False, False
        end = None
        for j in range(i, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            return  # unbalanced; nothing more to find
        yield i, text[i:end + 1]
        i = end + 1


def _as_command_list(obj):
    """Return obj as a list of command dicts, or None if it is not one."""
    if isinstance(obj, dict):
        obj = [obj]
    elif not isinstance(obj, list):
        return None
    for cmd in obj:
        if not isinstance(cmd, dict) or cmd.get("cmd") not in ALLOWED_CMDS:
            return None
    return obj


def split_reason(reply):
    """The reasoning text before the first command-JSON fragment (else the whole reply)."""
    for start, frag in _json_fragments(reply):
        try:
            obj = json.loads(frag)
        except ValueError:
            continue
        if _as_command_list(obj) is not None:
            pre = reply[:start]
            break
    else:
        pre = reply
    pre = re.sub(r"```", "", pre)
    pre = re.sub(r"\s+", " ", pre).strip()
    return re.sub(r"^(?:reason|REASON)\s*:\s*", "", pre)


def extract_commands(reply):
    """Parse the brain's reply into a list of command dicts.

    Returns a list (possibly empty, for "do nothing"), or None if the reply is not a
    usable JSON command. The brain is told to reply with a REASON line and then JSON,
    but a small model may still wrap it in fences or stray words (and the reasoning may
    itself contain brackets), so we scan for the first fragment that parses.
    """
    text = reply.strip()
    if "```" in text:  # drop a ```json ... ``` wrapper
        parts = text.split("```")
        inner = parts[1] if len(parts) >= 2 else text
        text = re.sub(r"^[a-zA-Z]*\s*", "", inner).strip()
    obj = None
    try:
        obj = json.loads(text)
    except ValueError:
        for _, frag in _json_fragments(text):
            try:
                cand = json.loads(frag)
            except ValueError:
                continue
            if _as_command_list(cand) is not None:
                obj = cand
                break
    if obj is None:
        print(f"[chatbot] reply is not a usable JSON command: {reply!r}")
        return None
    cmds = _as_command_list(obj)
    if cmds is None:
        print(f"[chatbot] refusing: {obj!r} is not a command object or a list of them")
        return None
    return cmds


def validate_command(cmd, link):
    """Client-side sanity check; the core still rejects anything unsafe."""
    name = cmd["cmd"]
    if name == "goto_zone":
        zone = cmd.get("zone")
        if link.zone_names and zone not in link.zone_names:
            print(f"[chatbot] refusing: zone {zone!r} not in the core's zone table")
            return False
    elif name == "set_mode":
        if cmd.get("mode") not in MODES:
            print(f"[chatbot] refusing: unknown mode {cmd.get('mode')!r}")
            return False
    elif name == "play_action":
        if not cmd.get("action"):
            print("[chatbot] refusing: play_action without an action name")
            return False
    return True


def handle_reply(reply, link, show_reason=True):
    """Show the brain's reasoning + JSON reply and forward its command(s) to the core."""
    reason = split_reason(reply)
    cmds = extract_commands(reply)
    if cmds is None:
        return  # extract_commands already printed the reason
    if show_reason and reason:
        print(f"robot> why: {reason}")
    if not cmds:
        print("robot> []  (no action)")
        return
    print(f"robot> {json.dumps(cmds)}")
    for cmd in cmds:
        if validate_command(cmd, link):
            print(f"[cmd] {json.dumps(cmd)}")
            link.send_command(cmd)


# ---------------------------------------------------------------------------
# The self-question loop.
# ---------------------------------------------------------------------------
def auto_poller(bot, link, interval, show_reason=True):
    """The brain periodically senses its own state and may act on its own."""
    while True:
        time.sleep(interval)
        try:
            reply = bot.ask(AMBIENT)
        except Exception as exc:
            print(f"[chatbot] auto: LLM error: {exc}")
            continue
        reason = split_reason(reply)
        cmds = extract_commands(reply)
        if cmds is None:
            continue
        if show_reason and reason:
            print(f"(auto) why: {reason}")
        if not cmds:
            print("(auto) []  (no action)")
            continue
        print(f"(auto) {json.dumps(cmds)}")
        for cmd in cmds:
            if validate_command(cmd, link):
                print(f"[cmd] {json.dumps(cmd)}")
                link.send_command(cmd)


# ---------------------------------------------------------------------------
# Zone table over the core's HTTP API.
# ---------------------------------------------------------------------------
def fetch_zone_table(http_url):
    url = http_url.rstrip("/") + "/api/zones"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.load(r)
    except Exception as exc:
        print(f"[chatbot] zone table unavailable ({exc}); "
              "the core will still reject bad commands")
        return None
    zones = data.get("zones") if isinstance(data, dict) else None
    return zones if isinstance(zones, dict) and zones else None


# ---------------------------------------------------------------------------
# REPL + selftest + main.
# ---------------------------------------------------------------------------
def repl(bot, link, show_reason=True):
    print("describe what you see/hear; the robot answers with a JSON action; 'exit' to leave")
    while True:
        try:
            user = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        user = user.strip()
        if not user:
            continue
        if user.lower() in ("exit", "quit"):
            break
        try:
            reply = bot.ask(user)
        except Exception as exc:
            print(f"LLM error: {exc}")
            continue
        handle_reply(reply, link, show_reason)


def selftest(link):
    """Check the core link (read-only), then exit."""
    print("[chatbot] selftest: waiting for a state frame from the core...")
    seen = {}

    def grab(frame):
        if frame.get("type") == "state":
            seen.update(frame)

    link.on_frame = grab
    t0 = time.time()
    while "zone" not in seen and time.time() - t0 < 15:
        time.sleep(0.1)
    if "zone" not in seen:
        print("[chatbot] selftest FAILED: no state frame received")
        return False
    print(f"[chatbot] selftest OK: zone={seen['zone']} mode={seen['mode']} "
          f"action={seen['action']} moving={seen['moving']}")
    return True


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="RTR chatbot - an LLM brain that chats and drives the robot core")
    p.add_argument("--ws-url", default="ws://127.0.0.1:8765",
                   help="core WebSocket URL (default: ws://127.0.0.1:8765)")
    p.add_argument("--http-url", default="http://127.0.0.1:8766",
                   help="core HTTP URL for the zone table (default: http://127.0.0.1:8766)")
    p.add_argument("--base-url", default="http://127.0.0.1:8888/v1",
                   help="OpenAI-compatible base URL (default: http://127.0.0.1:8888/v1)")
    p.add_argument("--api-key", default=os.environ.get("RTR_LLM_API_KEY", ""),
                   help="API key for the endpoint (or set RTR_LLM_API_KEY)")
    p.add_argument("--model",
                  default="unsloth/gemma-4-E4B-it-qat-mobile-GGUF",
                  help="model name the endpoint expects (must be LOADED in Unsloth Studio)")
    p.add_argument("--poll", type=float, default=20.0,
                  help="seconds between the brain's self-questions (0 = off)")
    p.add_argument("--temperature", type=float, default=0.8)
    # A generous budget: the default model is a *reasoning* model that spends tokens on
    # hidden thinking before it emits the visible reply (and any ROBOT> line).
    p.add_argument("--max-tokens", type=int, default=1500)
    p.add_argument("--selftest", action="store_true",
                   help="check the core link (read-only), then exit")
    p.add_argument("--no-reason", action="store_true",
                   help="do not print the brain's REASON line (default: print it)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    link = CoreLink(args.ws_url)
    link.start()

    if args.selftest:
        return 0 if selftest(link) else 1

    if not link.wait_connected(15.0):
        print("[chatbot] could not reach the core; is it running? (python main.py)")
        return 1

    bot = ChatBot(args.base_url, args.api_key, args.model, link,
                  temperature=args.temperature, max_tokens=args.max_tokens)

    def refresh():
        table = fetch_zone_table(args.http_url)
        bot.set_system_prompt(build_system_prompt(table))

    refresh()

    # Keep the prompt in sync if the core hot-reloads its zones.
    def on_frame(frame):
        if frame.get("type") == "zones_changed":
            threading.Thread(target=refresh, daemon=True).start()

    link.on_frame = on_frame

    show_reason = not args.no_reason

    if args.poll > 0:
        threading.Thread(target=auto_poller,
                          args=(bot, link, args.poll, show_reason),
                          name="auto-poller", daemon=True).start()
        print(f"[chatbot] self-questions every {args.poll:g} s")

    repl(bot, link, show_reason)
    print("[chatbot] bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
