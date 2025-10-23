from flask import Flask, redirect, jsonify, request
import requests, urllib.parse, os, threading

PPUBS_HOME  = "https://ppubs.uspto.gov/pubwebapp/static/pages/ppubsbasic.html"
PPUBS_SEARCH= "https://ppubs.uspto.gov/api/searches/generic"
PPUBS_PDF   = "https://ppubs.uspto.gov/api/pdf/downloadPdf/{doc_id}"

# In-memory token cache (process-local)
_token_lock = threading.Lock()
_cached_token = None

def get_cached_token():
    global _cached_token
    with _token_lock:
        return _cached_token

def set_cached_token(tok: str):
    global _cached_token
    with _token_lock:
        _cached_token = tok

app = Flask(__name__)

@app.get("/")
def index():
    return (
        "USPTO proxy is up.\n"
        "Use /patent/<doc_id>         -> auto-refresh token on 401 and redirect\n"
        "Use /patent_direct/<doc_id>  -> uses provided/cached token and redirects (no search)\n"
        "Use /debug/<doc_id>          -> diagnostics\n"
        "Use /set_token?token=XYZ&secret=... (needs TOKEN_SECRET)\n"
    ), 200

@app.get("/health")
def health():
    return "ok", 200

def browser_headers(extra=None):
    h = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://ppubs.uspto.gov",
        "referer": PPUBS_HOME,
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "accept-language": "en-US,en;q=0.9",
        "x-requested-with": "XMLHttpRequest",
        "pragma": "no-cache",
        "cache-control": "no-cache",
        # advisory hints
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    if extra:
        h.update(extra)
    return h

def search_body(doc_id: str):
    return {
        "cursorMarker": "*",
        "databaseFilters": [
            {"databaseName":"USPAT"},
            {"databaseName":"US-PGPUB"},
            {"databaseName":"USOCR"}
        ],
        "fields": ["documentId","patentNumber","title","datePublished","inventors","pageCount","type"],
        "op": "OR",
        "pageSize": 1,
        "q": f"({doc_id}).pn.",
        "searchType": 0,
        "sort": "date_publ desc"
    }

def warm_cookies(session: requests.Session):
    try:
        session.get(
            PPUBS_HOME,
            headers={
                "user-agent": browser_headers()["user-agent"],
                "accept-language": "en-US,en;q=0.9"
            },
            timeout=20
        )
    except Exception:
        pass

def build_pdf_url(doc_id: str, token: str) -> str:
    return PPUBS_PDF.format(doc_id=urllib.parse.quote(doc_id)) + "?requestToken=" + urllib.parse.quote(token)

def try_search(session: requests.Session, token: str|None, doc_id: str):
    """POST /searches/generic with optional token; return (status_code, new_token_or_None)."""
    headers = browser_headers()
    if token:
        headers["x-access-token"] = token
    r = session.post(PPUBS_SEARCH, json=search_body(doc_id), headers=headers, allow_redirects=False, timeout=30)
    return r.status_code, r.headers.get("x-access-token")

def ensure_fresh_token(doc_id: str, seed_token: str|None) -> str|None:
    """
    Returns a usable token (possibly refreshed) or None if unable.
    Strategy:
      1) Warm cookies.
      2) Try current token; if 401/403 or no new token, try placeholder to elicit a fresh token.
      3) If a token appears, cache & return it.
    """
    s = requests.Session()
    warm_cookies(s)

    # 1) Try with current token if present
    if seed_token:
        status, new_tok = try_search(s, seed_token, doc_id)
        if new_tok:
            set_cached_token(new_tok)
            return new_tok
        if status in (200, 201):
            # No new token but old might still be OK
            return seed_token
        # else fall through to placeholder

    # 2) Try with placeholder to coax a fresh token
    status2, new_tok2 = try_search(s, "placeholder", doc_id)
    if new_tok2:
        set_cached_token(new_tok2)
        return new_tok2

    # 3) As a last attempt, try without any token (some deployments issue on bare call)
    status3, new_tok3 = try_search(s, None, doc_id)
    if new_tok3:
        set_cached_token(new_tok3)
        return new_tok3

    return None

@app.get("/patent/<doc_id>")
def patent(doc_id):
    """
    Permanent link:
      - get cached (or hardcoded seed) token
      - ensure it's fresh (auto-renew on 401/403)
      - redirect to live PDF with the freshest token
    """
    token = get_cached_token()
    if not token:
        # seed once with a known-good token you captured from your browser (optional)
        token = "eyJzdWIiOiI2NDAzODQzYy02ODdjLTRlZjktOTJmYS0xYzA1ZmJiNWYxOWYiLCJ2ZXIiOiI0NjY3ODEzYy1kOTExLTRlOTgtOWY3My1jM2MwYWI4NmViMTIiLCJleHAiOjB9"
        set_cached_token(token)

    fresh = ensure_fresh_token(doc_id, token)
    if not fresh:
        # if we truly can't refresh, try with the old token anyway (may still succeed)
        return redirect(build_pdf_url(doc_id, token), code=302)

    return redirect(build_pdf_url(doc_id, fresh), code=302)

@app.get("/patent_direct/<doc_id>")
def patent_direct(doc_id):
    """
    Skip search: just use provided/cached token and redirect.
    Useful for manual tests with ?token=...
    """
    token = (request.args.get("token") or get_cached_token() or "").strip()
    if not token:
        return jsonify({"error": "No token available. Pass ?token=..."}), 400
    set_cached_token(token)
    return redirect(build_pdf_url(doc_id, token), code=302)

@app.get("/debug/<doc_id>")
def debug(doc_id):
    token = request.args.get("token") or get_cached_token()
    result = {"had_cached_token": bool(get_cached_token()), "used_query_token": bool(request.args.get("token"))}
    fresh = ensure_fresh_token(doc_id, token)
    result["fresh_token_obtained"] = bool(fresh)
    result["will_redirect_to"] = build_pdf_url(doc_id, fresh or (token or "<none>"))
    return jsonify(result)

@app.get("/set_token")
def set_token():
    secret_env = os.getenv("TOKEN_SECRET", "").strip()
    secret_fallback = "eyJzdWIiOiI2NDAzODQzYy02ODdjLTRlZjktOTJmYS0xYzA1ZmJiNWYxOWYiLCJ2ZXIiOiI5ZTBjMDZhNy0xMjQ0LTQwZTctOTk0Mi1kMzRhYzQwNzkxNGUiLCJleHAiOjB9"
    secret = secret_env or secret_fallback

    if not secret:
        return jsonify({"error": "Set TOKEN_SECRET env on the server to enable this endpoint."}), 400
    if request.args.get("secret") != secret:
        return jsonify({"error": "Unauthorized."}), 401

    tok = request.args.get("token", "").strip()
    if not tok:
        return jsonify({"error": "Missing token param"}), 400

    set_cached_token(tok)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
