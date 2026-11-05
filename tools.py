"""PumpGTM tools: schemas the model reads and handlers that call the REST API."""

import json

from .api import PumpGTMError, request

DECISIONS = ["send", "invite", "booking_link", "snooze", "start_sequence", "meeting_booked", "dismiss", "opt_out"]
SEEN_KEY = "seen_reply_ids"


def _obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


SCHEMAS = {
    "pumpgtm_sequences": {
        "description": "List the user's PumpGTM outreach sequences (LinkedIn, email, X) with status, "
                       "channels and lead counts. Pass query to find one by name.",
        "parameters": _obj({"query": {"type": "string", "description": "Optional name filter, case-insensitive."}}),
    },
    "pumpgtm_leads": {
        "description": "List PumpGTM leads (people in outreach) with their stage. Use stage='replied' or "
                       "booked=true to see who engaged, q to search a person or company.",
        "parameters": _obj({
            "stage": {"type": "string", "description": "queued, invited, connected, messaged, replied or meeting_booked."},
            "q": {"type": "string", "description": "Search person or company."},
            "booked": {"type": "boolean", "description": "Only people who booked a meeting."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
        }),
    },
    "pumpgtm_progress": {
        "description": "Outreach results: funnel per sequence (leads, invited, accepted, messaged, replied, "
                       "meetings, rates) plus the LinkedIn pace over the last since_days days.",
        "parameters": _obj({"since_days": {"type": "integer", "minimum": 1, "default": 7}}),
    },
    "pumpgtm_replies": {
        "description": "Prospect replies waiting for a decision, each with their message and the drafted answer. "
                       "only_new=true returns only replies not reported before (used by the scheduled digest).",
        "parameters": _obj({
            "only_new": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        }),
    },
    "pumpgtm_decide_reply": {
        "description": "Act on one pending reply: send (the draft, or text to replace it), invite, booking_link, "
                       "snooze (days), start_sequence, meeting_booked, dismiss or opt_out. send, invite, "
                       "booking_link and start_sequence message a real person. Hermes shows the user the person and the "
                       "exact text and asks them to approve before the call runs, so call it directly when they ask.",
        "parameters": _obj({
            "reply_id": {"type": "string", "description": "The reply id from pumpgtm_replies."},
            "decision": {"type": "string", "enum": DECISIONS},
            "text": {"type": "string", "description": "send only: replaces the drafted answer."},
            "days": {"type": "integer", "minimum": 1, "description": "snooze only."},
        }, required=["reply_id", "decision"]),
    },
    "pumpgtm_enrich_person": {
        "description": "Look up one person by work email or LinkedIn URL: name, title, company, LinkedIn, X. "
                       "find can add paid extras ('x', 'work_email'). Nothing is sent to the person.",
        "parameters": _obj({
            "email": {"type": "string"},
            "linkedin": {"type": "string", "description": "linkedin.com/in/... URL."},
            "find": {"type": "array", "items": {"type": "string", "enum": ["x", "work_email"]}, "maxItems": 2},
        }),
    },
}


def compact_reply(r):
    lead = r.get("lead") or {}
    return {
        "id": r.get("id"), "channel": r.get("channel"), "person": lead.get("person"),
        "company": lead.get("company"), "title": lead.get("title"), "classification": r.get("classification"),
        "their_message": r.get("theirMessage"), "draft": r.get("draft"), "replied_at": r.get("repliedAt"),
        "allowed_decisions": r.get("allowedDecisions"),
    }


def pending_replies(ws, limit=50):
    return [compact_reply(r) for r in request("GET", "/replies", {"limit": limit}, workspace_id=ws).get("replies", [])]


def find_reply(ws, reply_id):
    """Resolve a full id or a unique prefix (the digest shows 8 characters) to a pending reply."""
    rid = (reply_id or "").strip().lower()
    matches = [r for r in pending_replies(ws, 200) if rid and (r["id"] or "").startswith(rid)]
    return matches[0] if len(matches) == 1 else None


def decide(ws, reply_id, decision, text="", days=None, by="hermes"):
    body = {"decision": decision, "by": by}
    if text:
        body["text"] = text
    if days:
        body["days"] = int(days)
    return request("POST", f"/replies/{reply_id}/decide", body=body, workspace_id=ws)


def make_handlers(ctx):
    ws = lambda: str(ctx.get_config("workspace_id", default="") or "")

    def sequences(args):
        q = (args.get("query") or "").lower()
        rows = request("GET", "/sequences", workspace_id=ws()).get("sequences", [])
        return {"sequences": [
            {"id": s.get("id"), "name": s.get("name"), "status": s.get("status"), "lead_count": s.get("leadCount"),
             "channels": s.get("channels"), "steps": len(s.get("steps") or [])}
            for s in rows if q in (s.get("name") or "").lower()]}

    def leads(args):
        data = request("GET", "/leads", {
            "stage": args.get("stage"), "q": args.get("q"), "booked": "1" if args.get("booked") else None,
            "limit": int(args.get("limit") or 25)}, workspace_id=ws())
        keep = ("id", "person", "company", "title", "stage", "sequenceId", "linkedinUrl", "email",
                "xUsername", "repliedAt", "meetingBookedAt")
        return {"count": data.get("count"), "leads": [{k: l.get(k) for k in keep} for l in data.get("leads", [])]}

    def progress(args):
        funnel = request("GET", "/reports/funnel", workspace_id=ws())
        pace = request("GET", "/progress", {"sinceDays": int(args.get("since_days") or 7)}, workspace_id=ws())
        return {
            "total": funnel.get("total"),
            "by_sequence": [{"name": g.get("name"), **(g.get("funnel") or {})} for g in funnel.get("groups", [])],
            "last_days": pace.get("period", {}).get("days"), "recent": pace.get("funnel"),
            "queued": pace.get("queued"), "accounts": pace.get("accounts"),
        }

    def replies(args):
        rows = pending_replies(ws(), int(args.get("limit") or 20))
        if args.get("only_new"):
            seen = list(ctx.state.get(SEEN_KEY, default=[]) or [])
            rows = [r for r in rows if r["id"] not in seen]
            ctx.state.set(SEEN_KEY, (seen + [r["id"] for r in rows])[-1000:])
        return {"count": len(rows), "replies": rows}

    def decide_reply(args):
        result = decide(ws(), args["reply_id"], args["decision"], args.get("text") or "", args.get("days"))
        return {"ok": True, "result": result}

    def enrich_person(args):
        body = {k: args[k] for k in ("email", "linkedin") if args.get(k)}
        if len(body) != 1:
            return {"error": "Pass exactly one of email or linkedin."}
        if args.get("find"):
            body["find"] = args["find"]
        return request("POST", "/people/enrich", body=body, workspace_id=ws())

    def wrap(fn):
        def handler(args, **kwargs):
            try:
                return json.dumps(fn(args or {}))
            except PumpGTMError as exc:
                return json.dumps({"error": exc.message, "status": exc.status})
            except Exception as exc:  # never raise into the agent loop
                return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        return handler

    fns = {"pumpgtm_sequences": sequences, "pumpgtm_leads": leads, "pumpgtm_progress": progress,
           "pumpgtm_replies": replies, "pumpgtm_decide_reply": decide_reply, "pumpgtm_enrich_person": enrich_person}
    return {name: wrap(fn) for name, fn in fns.items()}
