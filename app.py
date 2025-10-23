from flask import Flask, redirect
import requests, urllib.parse

app = Flask(__name__)

@app.route("/patent/<doc_id>")
def jump(doc_id):
    # Step 1: Call USPTO search API to get a fresh token
    search_body = {
        "cursorMarker": "*",
        "databaseFilters": [
            {"databaseName": "USPAT"},
            {"databaseName": "US-PGPUB"},
            {"databaseName": "USOCR"}
        ],
        "fields": ["documentId"],
        "op": "OR",
        "pageSize": 1,
        "q": f"({doc_id}).pn.",
        "searchType": 0,
        "sort": "date_publ desc"
    }

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "origin": "https://ppubs.uspto.gov",
        "user-agent": "Mozilla/5.0"
    }

    r = requests.post("https://ppubs.uspto.gov/api/searches/generic", json=search_body, headers=headers)
    r.raise_for_status()

    # Extract token (if available in headers or JSON)
    token = r.headers.get("x-access-token")
    if not token:
        return "Token not found; USPTO may have changed their headers.", 500

    # Step 2: Redirect to live USPTO PDF
    pdf_url = f"https://ppubs.uspto.gov/api/pdf/downloadPdf/{doc_id}?requestToken={urllib.parse.quote(token)}"
    return redirect(pdf_url, code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
