"""PumpGTM for Hermes: outreach tools, an approval gate on reply decisions, a /pumpgtm chat command
and a scheduled reply digest that lands in the user's messaging channel."""

import hashlib

from .api import PumpGTMError
from .tools import SCHEMAS, decide, find_reply, make_handlers, pending_replies

GATED_TOOL = "pumpgtm_decide_reply"

DIGEST_PROMPT = (
    "Call the pumpgtm_replies tool with only_new=true. If it returns count 0, respond with exactly [SILENT]. "
    "Otherwise write one short digest. For each reply: the person, title, company and channel; what they said; "
    "the drafted answer; and the line `/pumpgtm send <first 8 characters of the reply id>` so the user can "
    "approve it from this chat. Never call pumpgtm_decide_reply in this run."
)


def _who(reply):
    return f"{reply.get('person')} ({reply.get('company')}) on {reply.get('channel')}"


def _digest(replies):
    if not replies:
        return "No PumpGTM replies are waiting."
    lines = [f"{len(replies)} PumpGTM repl{'y' if len(replies) == 1 else 'ies'} waiting:"]
    for r in replies:
        lines += ["", f"{r['id'][:8]}  {r.get('person')}, {r.get('title')} at {r.get('company')} ({r.get('channel')})",
                  f"They said: {r.get('their_message')}", f"Draft: {r.get('draft')}", f"Approve: /pumpgtm send {r['id'][:8]}"]
    return "\n".join(lines)


def register(ctx):
    ws = lambda: str(ctx.get_config("workspace_id", default="") or "")

    handlers = make_handlers(ctx)
    for name, schema in SCHEMAS.items():
        ctx.register_tool(name=name, toolset="pumpgtm", schema={"name": name, **schema}, handler=handlers[name],
                          emoji="📣")

    def guard_reply_decision(tool_name="", args=None, **kwargs):
        """Scheduled runs never act on a reply; every other call goes through Hermes's approval gate."""
        if tool_name != GATED_TOOL:
            return None
        if ctx.current_cron_execution() is not None:
            return {"action": "block", "message": "PumpGTM reply decisions are never made from a scheduled run. "
                                                  "Ask the user to approve with /pumpgtm send <id>."}
        args = args or {}
        rid, decision = str(args.get("reply_id") or ""), str(args.get("decision") or "")
        try:
            reply = find_reply(ws(), rid)
        except PumpGTMError:
            reply = None
        target = _who(reply) if reply else f"reply {rid}"
        text = args.get("text") or (reply or {}).get("draft") or ""
        message = f'Send to {target}: "{text}"' if decision == "send" else f"{decision} on {target}"
        digest = hashlib.sha256(f"{rid}|{decision}|{text}".encode()).hexdigest()[:16]
        # A per-call rule key: "always allow" can never cover a different reply or different text.
        return {"action": "approve", "message": message, "rule_key": f"pumpgtm:{digest}"}

    ctx.register_hook("pre_tool_call", guard_reply_decision)

    def pumpgtm_command(raw_args: str) -> str:
        parts = (raw_args or "").split()
        try:
            if not parts or parts[0] in ("replies", "digest"):
                return _digest(pending_replies(ws(), 20))
            if parts[0] == "send" and len(parts) == 2:
                reply = find_reply(ws(), parts[1])
                if not reply:
                    return f"No single pending reply matches {parts[1]}. Run /pumpgtm replies."
                pick = ctx.ask_choice(f'Send to {_who(reply)}?\n\n"{reply.get("draft")}"',
                                      [{"value": "send", "label": "Send"}, {"value": "cancel", "label": "Cancel"}])
                if pick != "send":
                    return "Not sent."
                decide(ws(), reply["id"], "send", by="hermes:/pumpgtm")
                return f"Sent to {_who(reply)}."
        except PumpGTMError as exc:
            return f"PumpGTM refused: {exc.message}"
        return "Usage: /pumpgtm replies | /pumpgtm send <reply id>"

    ctx.register_command("pumpgtm", pumpgtm_command, description="PumpGTM replies waiting, and approve one",
                         args_hint="[replies | send <id>]")

    ctx.register_automation_blueprint(
        "reply-digest",
        title="PumpGTM reply digest",
        description="Checks PumpGTM for new prospect replies and posts each one with its drafted answer to your "
                    "chat, so you can approve it there with /pumpgtm send.",
        category="work",
        schedule_template="*/{interval_min} * * * *",
        prompt_template=DIGEST_PROMPT,
        slots=[
            {"name": "interval_min", "type": "enum", "label": "How often?", "default": "15",
             "options": ["15", "30", "60"], "help": "minutes between checks"},
            {"name": "deliver", "type": "enum", "label": "Where to deliver?", "default": "origin",
             "options": ["origin", "telegram", "slack", "discord", "whatsapp"], "strict": False},
        ],
        tags=["pumpgtm", "sales", "replies"],
    )
