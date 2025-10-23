from flask import Flask, redirect, jsonify, Response
import requests, urllib.parse

PPUBS_HOME = "https://ppubs.uspto.gov/pubwebapp/static/pages/ppubsbasic.html"
PPUBS_SEARCH = "https://ppubs.uspto.gov/api/searches/generic"
PPUBS_PDF = "https://ppubs.uspto.gov/api/pdf/downloadPdf/{doc_id}"

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
    }
    if extra:
        h.update(extra)
    return h

def search_body(doc_id: str):
    return {
        "cursorMarker": "*",
        "databaseFilters": [{"databaseName":"USPAT"},{"databaseName":"US-PGPUB"},{"databaseName":"USOCR"}],
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

def fetch_token_and_redirect(doc_id: str):
    s = requests.Session()

    # 0) Warm cookies by loading the public page
    s.get(PPUBS_HOME, headers={"user-agent": browser_headers()["user-agent"], "accept-language": "en-US,en;q=0.9"}, timeout=20)

    # 1) First attempt (no token)
    r1 = s.post(PPUBS_SEARCH, json=search_body(doc_id), headers=browser_headers(), allow_redirects=False, timeout=30)
    tok = r1.headers.get("x-access-token")

    # 2) If unauthorized, try with a dummy token to elicit a real one
    if (r1.status_code in (401,403)) and not tok:
        r2 = s.post(PPUBS_SEARCH, json=search_body(doc_id), headers=browser_headers({"x-access-token":"placeholder"}), allow_redirects=False, timeout=30)
        tok = r2.headers.get("x-access-token")
        last = r2
    else:
        last = r1

    # 3) If we have a token, try one more POST with it (sometimes refreshes token)
    if tok:
        r3 = s.post(PPUBS_SEARCH, json=search_body(doc_id), headers=browser_headers({"x-access-token":tok}), allow_redirects=False, timeout=30)
        tok = r3.headers.get("x-access-token") or tok
        last = r3

    # 4) If still no token, return diagnostics
    if not tok:
        info = {
            "step1_status": r1.status_code,
            "step1_headers": dict(r1.headers),
        }
        try:
            info["step1_body_snippet"] = r1.text[:500]
        except Exception:
            pass
        return None, info

    # 5) Redirect to PDF with token
    pdf_url = PPUBS_PDF.format(doc_id=urllib.parse.quote(doc_id)) + "?requestToken=" + urllib.parse.quote(tok)
    return pdf_url, None

@app.route("/patent/<doc_id>")
def patent(doc_id):
    pdf_url, diag = fetch_token_and_redirect(doc_id)
    if pdf_url:
        return redirect(pdf_url, code=302)
    return jsonify({"error": "Could not obtain token", "details": diag}), 502

@app.route("/debug/<doc_id>")
def debug(doc_id):
    pdf_url, diag = fetch_token_and_redirect(doc_id)
    if pdf_url:
        return jsonify({"success": True, "redirect_to": pdf_url})
    return jsonify({"success": False, "details": diag}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
