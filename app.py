from flask import Flask, redirect, jsonify, request
import requests, urllib.parse, os, threading

PPUBS_HOME = "https://ppubs.uspto.gov/pubwebapp/static/pages/ppubsbasic.html"
PPUBS_SEARCH = "https://ppubs.uspto.gov/api/searches/generic"
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
        "Use /patent/<doc_id>         -> tries to refresh token via search, then redirects\n"
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
        # These are advisory; many servers ignore them server-side
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

def try_search_with_token(session: requests.Session, token: str, doc_id: str):
    r = session.post(
        PPUBS_SEARCH,
        json=search_body(doc_id),
        headers=browser_headers({"x-access-token": token}),
        allow_redirects=False,
        timeout=30
    )
    new_tok = r.headers.get("x-access-token")
    return r, (new_tok or token)

def build_pdf_url(doc_id: str, token: str) -> str:
    return PPUBS_PDF.format(doc_id=urllib.parse.quote(doc_id)) + "?requestToken=" + urllib.parse.quote(token)

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

def fetch_pdf_redirect(doc_id: str):
    """
    Full flow: try existing token (query/cached/env), else try to elicit one via search POSTs.
    Returns (redirect_url, diag_info or None)
    """
    # Token priority: query param > cached > env var
    token = (request.args.get("token") or
             get_cached_token() or
             os.getenv("X_ACCESS_TOKEN", "").strip())

    s = requests.Session()
    warm_cookies(s)

    # If we already have a token, try a search POST to refresh it (best effort),
    # then redirect with whichever token we have.
    if token:
        r, token = try_search_with_token(s, token, doc_id)
        if token:
            set_cached_token(token)
            return build_pdf_url(doc_id, token), {"status": r.status_code, "had_token": True}

    # No token yet: try to elicit one
    r1 = s.post(PPUBS_SEARCH, json=search_body(doc_id),
                headers=browser_headers(), allow_redirects=False, timeout=30)
    tok = r1.headers.get("x-access-token")

    if (r1.status_code in (401, 403)) and not tok:
        r2 = s.post(PPUBS_SEARCH, json=search_body(doc_id),
                    headers=browser_headers({"x-access-token": "placeholder"}),
                    allow_redirects=False, timeout=30)
        tok = r2.headers.get("x-access-token")
        last = r2
    else:
        last = r1

    if tok:
        set_cached_token(tok)
        return build_pdf_url(doc_id, tok), {"status": last.status_code, "had_token": False}

    # fail with diagnostics
    info = {
        "step1_status": r1.status_code,
        "step1_headers": dict(r1.headers),
        "hint": "Provide a working x-access-token via env X_ACCESS_TOKEN or /set_token?token=..."
    }
    try:
        info["step1_body_snippet"] = r1.text[:500]
    except Exception:
        pass
    return None, info

@app.get("/patent/<doc_id>")
def patent(doc_id):
    # Use cached token (seeded once via /set_token or first browser call)
    token = get_cached_token()
    if not token:
        # fallback: load once from your known good token at startup
        token = "eyJzdWIiOiI2NDAzODQzYy02ODdjLTRlZjktOTJmYS0xYzA1ZmJiNWYxOWYiLCJ2ZXIiOiI0NjY3ODEzYy1kOTExLTRlOTgtOWY3My1jM2MwYWI4NmViMTIiLCJleHAiOjB9"
        set_cached_token(token)

    # 1️⃣ Try the existing token on /api/searches/generic to refresh it
    s = requests.Session()
    body = search_body(doc_id)
    r = s.post(PPUBS_SEARCH, json=body, headers=browser_headers({"x-access-token": token}))
    new_tok = r.headers.get("x-access-token")
    if new_tok:
        set_cached_token(new_tok)
        token = new_tok

    # 2️⃣ Redirect to PDF using the freshest token
    pdf_url = build_pdf_url(doc_id, token)
    return redirect(pdf_url, code=302)


@app.get("/patent_direct/<doc_id>")
def patent_direct(doc_id):
    """
    Skip the search step and just redirect using a provided/cached/env token.
    Useful for local testing when you copy a working x-access-token from the browser.
    """
    token = (request.args.get("token") or
             get_cached_token() or
             os.getenv("X_ACCESS_TOKEN", "").strip())
    if not token:
        return jsonify({"error": "No token available. Pass ?token=... or set X_ACCESS_TOKEN env."}), 400
    set_cached_token(token)
    return redirect(build_pdf_url(doc_id, token), code=302)

@app.get("/debug/<doc_id>")
def debug(doc_id):
    url, diag = fetch_pdf_redirect(doc_id)
    if url:
        return jsonify({"success": True, "redirect_to": url})
    return jsonify({"success": False, "details": diag})

@app.get("/set_token")
def set_token():
    # Prefer env var; fall back to the value you pasted so it still works if env unset
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
