"""Small PumpGTM REST client (stdlib only). The workspace key never leaves this module."""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://app.pumpgtm.com/api/v1"
USER_AGENT = "hermes-pumpgtm/1.0.0"


class PumpGTMError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _api_key():
    from agent.secret_scope import get_secret
    key = (get_secret("PUMPGTM_API_KEY") or "").strip()
    if not key:
        raise PumpGTMError(0, "PUMPGTM_API_KEY is not set. Create a workspace key at https://app.pumpgtm.com/install-mcp")
    return key


def request(method, path, query=None, body=None, workspace_id=""):
    """Call the PumpGTM API and return the decoded JSON body, or raise PumpGTMError."""
    query = {k: v for k, v in (query or {}).items() if v not in (None, "", False)}
    url = BASE_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"Authorization": "Bearer " + _api_key(), "Accept": "application/json", "User-Agent": USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if workspace_id:
        headers["X-PumpGTM-Client"] = workspace_id
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        try:
            parsed = json.loads(detail)
            detail = parsed.get("message") or parsed.get("error") or parsed.get("outcome") or detail
        except ValueError:
            pass
        raise PumpGTMError(exc.code, str(detail)) from None
    except urllib.error.URLError as exc:
        raise PumpGTMError(0, f"Could not reach PumpGTM: {exc.reason}") from None
