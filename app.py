from urllib.parse import urlparse
import os

from flask import Flask, abort, render_template, request, send_file, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Trust reverse proxy headers so generated links can stay HTTPS behind a proxy/CDN.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSION = ".apk"
APK_MIME_TYPE = "application/vnd.android.package-archive"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["PREFERRED_URL_SCHEME"] = "https"


def _allowed_download_domains():
    domains = {
        domain.strip().lower()
        for domain in os.environ.get("ALLOWED_DOWNLOAD_DOMAINS", "").split(",")
        if domain.strip()
    }

    # Always allow this app's own host.
    if request.host:
        domains.add(request.host.split(":", 1)[0].lower())

    return domains


def _is_allowed_hotlink_request():
    referer = request.headers.get("Referer", "").strip()
    origin = request.headers.get("Origin", "").strip()

    # Many mobile browsers / in-app browsers omit referer. Treat those as valid.
    if not referer and not origin:
        return True

    for header_value in (referer, origin):
        if not header_value:
            continue

        parsed = urlparse(header_value)
        hostname = (parsed.hostname or "").lower()

        if hostname and hostname in _allowed_download_domains():
            return True

    return False


@app.route("/")
def home():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files.get("file")

    if not file or not file.filename:
        return "No file selected!", 400

    filename = secure_filename(file.filename)

    if not filename.lower().endswith(ALLOWED_EXTENSION):
        return "Only APK files allowed!", 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    download_link = url_for(
        "download_file",
        filename=filename,
        _external=True,
        _scheme="https",
    )

    return f"""
    APK Uploaded Successfully ✅ <br><br>
    Direct Download Link:<br>
    <a href="{download_link}">{download_link}</a>
    """


@app.route("/download/<path:filename>")
def download_file(filename):
    if not _is_allowed_hotlink_request():
        return "403 Forbidden: Hotlinking is not allowed.", 403

    safe_name = secure_filename(filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)

    if not os.path.isfile(filepath):
        abort(404)

    response = send_file(
        filepath,
        mimetype=APK_MIME_TYPE,
        as_attachment=True,
        download_name=safe_name,
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"

    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
