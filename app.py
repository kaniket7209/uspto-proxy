from flask import Flask, redirect, request, jsonify
import requests, urllib.parse

PPUBS_SEARCH = "https://ppubs.uspto.gov/api/searches/generic"
PPUBS_PDF = "https://ppubs.uspto.gov/api/pdf/downloadPdf/{doc_id}"

def default_headers():
    # Try to mimic a real browser as closely as possible
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://ppubs.uspto.gov",
        "referer": "https://ppubs.uspto.gov/pubwebapp/static/pages/ppubsbasic.html",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-platform": '"macOS"',
        "sec-ch-ua-mobile": "?0",
    }

def search_body_for_doc(doc_id: str):
    return {
        "cursorMarker": "*",
        "databaseFilters": [{"databaseName": "USPAT"}, {"databaseName": "US-PGPUB"}, {"databaseName": "USOCR"}],
        "fields": ["documentId","patentNumber","title","datePublished","inventors","pageCount","type"],
        "op": "OR",
        "pageSize": 1,
        "q": f"({doc_id}).pn.",
        "searchType": 0,
        "sort": "date_publ desc"
    }

app = Flask(__name__)

@app.route("/health")
def health():
    return "ok", 200

@app.route("/patent/<doc_id>")
def proxy(doc_id):
    s = requests.Session()
    headers = default_headers()

    # 1) First attempt without token (some servers return a fresh x-access-token header with 401)
    r = s.post(PPUBS_SEARCH, json=search_body_for_doc(doc_id), headers=headers, allow_redirects=False)
    token = r.headers.get("x-access-token")

    # If unauthorized and token was provided by server, retry with token
    if (r.status_code == 401 or r.status_code == 403) and token:
        headers["x-access-token"] = token
        r = s.post(PPUBS_SEARCH, json=search_body_for_doc(doc_id), headers=headers, allow_redirects=False)

    # Last chance: if still unauthorized but server sent a token in this response, try once more
    if (r.status_code == 401 or r.status_code == 403) and not headers.get("x-access-token"):
        token = r.headers.get("x-access-token")
        if token:
            headers["x-access-token"] = token
            r = s.post(PPUBS_SEARCH, json=search_body_for_doc(doc_id), headers=headers, allow_redirects=False)

    # If we have a token (from either attempt), build the download URL using it.
    # Empirically, the same header token often works as requestToken for download.
    token = headers.get("x-access-token") or r.headers.get("x-access-token")
    if not token:
        # As a fallback, try to read a requestToken-like value from JSON (if present in future API changes)
        try:
            js = r.json()
            token = js.get("requestToken") or js.get("token")
        except Exception:
            pass

    if not token:
        return (f"Could not obtain access token from USPTO (status={r.status_code}). "
                f"Try again or visit ppubs.uspto.gov manually.", 502)

    # 2) Redirect the client to ppubs with the (fresh) token—address bar shows ppubs.uspto.gov
    pdf_url = PPUBS_PDF.format(doc_id=urllib.parse.quote(doc_id))
    pdf_url = f"{pdf_url}?requestToken={urllib.parse.quote(token)}"
    return redirect(pdf_url, code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
