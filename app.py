import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DATABASE_PATH = BASE_DIR / "database.db"
LATEST_APK_NAME = "latest.apk"


def create_app() -> Flask:
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        MAX_CONTENT_LENGTH=int(
            os.environ.get("MAX_UPLOAD_BYTES", 600 * 1024 * 1024)
        ),  # 600MB default
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()
        == "true",
        UPLOAD_FOLDER=str(UPLOAD_DIR),
        DOWNLOAD_CACHE_SECONDS=int(os.environ.get("DOWNLOAD_CACHE_SECONDS", "300")),
        ALLOWED_DOWNLOAD_HOSTS=os.environ.get("ALLOWED_DOWNLOAD_HOSTS", "https://www.chillkaro.in/"),
    )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    @app.before_request
    def load_current_user() -> None:
        g.admin_user = session.get("admin_user")

    @app.context_processor
    def inject_globals():
        return {"admin_user": g.get("admin_user")}

    @app.route("/")
    def index():
        return redirect(url_for("download_latest"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if g.admin_user:
            return redirect(url_for("admin_dashboard"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = get_admin_by_username(username)

            if admin and check_password_hash(admin["password_hash"], password):
                session.clear()
                session["admin_user"] = admin["username"]
                session.permanent = True
                flash("Logged in successfully.", "success")
                return redirect(url_for("admin_dashboard"))

            flash("Invalid credentials.", "danger")

        return render_template("login.html")

    @app.route("/admin/logout", methods=["POST"])
    @login_required
    def admin_logout():
        session.clear()
        flash("Logged out successfully.", "info")
        return redirect(url_for("admin_login"))

    @app.route("/admin/dashboard", methods=["GET", "POST"])
    @login_required
    def admin_dashboard():
        if request.method == "POST":
            uploaded_file = request.files.get("apk_file")
            if not uploaded_file or uploaded_file.filename == "":
                flash("Please select an APK file to upload.", "danger")
                return redirect(url_for("admin_dashboard"))

            if not is_allowed_apk(uploaded_file.filename):
                flash("Only .apk files are allowed.", "danger")
                return redirect(url_for("admin_dashboard"))

            safe_original_name = secure_filename(uploaded_file.filename)
            temp_path = UPLOAD_DIR / f".{LATEST_APK_NAME}.uploading"
            uploaded_file.save(temp_path)

            if not is_valid_apk_file(temp_path):
                temp_path.unlink(missing_ok=True)
                flash("Uploaded file is not a valid APK archive.", "danger")
                return redirect(url_for("admin_dashboard"))

            file_size = temp_path.stat().st_size
            file_sha256 = sha256sum(temp_path)
            final_path = UPLOAD_DIR / LATEST_APK_NAME
            temp_path.replace(final_path)

            update_apk_metadata(
                original_name=safe_original_name,
                storage_name=LATEST_APK_NAME,
                file_size=file_size,
                sha256=file_sha256,
            )

            flash("APK uploaded successfully. /download now serves the latest version.", "success")
            return redirect(url_for("admin_dashboard"))

        stats = get_stats()
        current_apk = get_current_apk()
        return render_template(
            "dashboard.html",
            stats=stats,
            current_apk=current_apk,
            max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        )

    @app.route("/admin/stats")
    @login_required
    def admin_stats():
        history = get_download_history(limit=100)
        stats = get_stats()
        return render_template("stats.html", history=history, stats=stats)

    @app.route("/download")
    def download_latest():
        latest_path = UPLOAD_DIR / LATEST_APK_NAME
        if not latest_path.exists():
            abort(404, description="No APK has been uploaded yet.")

        if should_redirect_to_https(request):
            return redirect(request.url.replace("http://", "https://", 1), code=302)

        if is_hotlink_blocked(request):
            abort(403, description="Hotlinking is not allowed.")

        log_download(
            ip=request.headers.get("CF-Connecting-IP", request.remote_addr),
            user_agent=request.headers.get("User-Agent", ""),
            referer=request.headers.get("Referer", ""),
        )

        metadata = get_current_apk()
        download_name = metadata["original_name"] if metadata else LATEST_APK_NAME
        response = send_file(
            latest_path,
            mimetype="application/vnd.android.package-archive",
            as_attachment=True,
            download_name=download_name,
            conditional=True,
            etag=True,
            max_age=app.config["DOWNLOAD_CACHE_SECONDS"],
        )
        cache_ttl = app.config["DOWNLOAD_CACHE_SECONDS"]
        response.headers["Content-Type"] = "application/vnd.android.package-archive"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = (
            f"public, max-age={cache_ttl}, s-maxage={cache_ttl}, must-revalidate"
        )
        response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
        return response

    @app.errorhandler(403)
    def handle_403(error):
        return render_template("error.html", code=403, message=str(error)), 403

    @app.errorhandler(404)
    def handle_404(error):
        return render_template("error.html", code=404, message=str(error)), 404

    @app.errorhandler(413)
    def handle_413(_error):
        max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return (
            render_template(
                "error.html",
                code=413,
                message=f"Uploaded file is too large. Max upload size is {max_mb}MB.",
            ),
            413,
        )

    @app.errorhandler(500)
    def handle_500(_error):
        return (
            render_template(
                "error.html",
                code=500,
                message="An unexpected error occurred. Please try again.",
            ),
            500,
        )

    return app


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_user"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def run_db(query: str, params: tuple = (), commit: bool = False):
    db = get_db()
    cursor = db.execute(query, params)
    if commit:
        db.commit()
    return cursor


def init_db() -> None:
    db = sqlite3.connect(DATABASE_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS apk_metadata (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            original_name TEXT NOT NULL,
            storage_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS download_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            downloaded_at TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            referer TEXT
        )
        """
    )

    admin_username = os.environ.get("ADMIN_USERNAME", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "change-me-now")
    existing_admin = db.execute(
        "SELECT id FROM admins WHERE username = ?", (admin_username,)
    ).fetchone()
    if not existing_admin:
        db.execute(
            "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
            (
                admin_username,
                generate_password_hash(admin_password),
                utc_now_iso(),
            ),
        )

    db.commit()
    db.close()


def get_admin_by_username(username: str):
    return run_db("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()


def update_apk_metadata(original_name: str, storage_name: str, file_size: int, sha256: str):
    run_db(
        """
        INSERT INTO apk_metadata (id, original_name, storage_name, file_size, sha256, uploaded_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            original_name = excluded.original_name,
            storage_name = excluded.storage_name,
            file_size = excluded.file_size,
            sha256 = excluded.sha256,
            uploaded_at = excluded.uploaded_at
        """,
        (original_name, storage_name, file_size, sha256, utc_now_iso()),
        commit=True,
    )


def get_current_apk():
    return run_db("SELECT * FROM apk_metadata WHERE id = 1").fetchone()


def log_download(ip: str | None, user_agent: str, referer: str):
    run_db(
        """
        INSERT INTO download_events (downloaded_at, ip_address, user_agent, referer)
        VALUES (?, ?, ?, ?)
        """,
        (utc_now_iso(), ip, user_agent[:512], referer[:1024]),
        commit=True,
    )


def get_stats() -> dict:
    total_downloads = run_db("SELECT COUNT(*) AS total FROM download_events").fetchone()["total"]
    last_download = run_db(
        "SELECT downloaded_at FROM download_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "total_downloads": total_downloads,
        "last_download": last_download["downloaded_at"] if last_download else None,
    }


def get_download_history(limit: int = 100):
    return run_db(
        """
        SELECT downloaded_at, ip_address, user_agent, referer
        FROM download_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def is_allowed_apk(filename: str) -> bool:
    return "." in filename and filename.lower().endswith(".apk")


def is_valid_apk_file(path: Path) -> bool:
    if path.stat().st_size == 0:
        return False
    with path.open("rb") as file_obj:
        signature = file_obj.read(2)
    return signature == b"PK"


def is_hotlink_blocked(req) -> bool:
    allowed_hosts = get_allowed_download_hosts(req)
    referer = req.headers.get("Referer")
    origin = req.headers.get("Origin")

    if referer and not is_allowed_source_host(referer, allowed_hosts):
        return True

    if origin and not is_allowed_source_host(origin, allowed_hosts):
        return True

    return False


def get_allowed_download_hosts(req) -> set[str]:
    configured_hosts = current_app.config.get("ALLOWED_DOWNLOAD_HOSTS", "")
    configured = {
        normalize_host(candidate)
        for candidate in configured_hosts.split(",")
        if candidate.strip()
    }
    runtime_hosts = {
        normalize_host(req.host),
        normalize_host(req.headers.get("X-Forwarded-Host", "")),
    }
    return {host for host in configured.union(runtime_hosts) if host}


def normalize_host(host: str) -> str:
    return host.split(":", 1)[0].strip().lower()


def is_allowed_source_host(source_url: str, allowed_hosts: set[str]) -> bool:
    host = normalize_host(urlparse(source_url).netloc)
    if not host:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def should_redirect_to_https(req) -> bool:
    proto = req.headers.get("X-Forwarded-Proto", "").lower()
    is_secure_request = req.is_secure or proto == "https"
    host = normalize_host(req.host)
    is_local = host in {"localhost", "127.0.0.1"}
    return not is_secure_request and not is_local


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


app = create_app()


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
