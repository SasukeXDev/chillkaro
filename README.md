# Secure APK Hosting (Flask)

Production-ready Flask application for securely hosting a single **latest APK** behind a permanent download URL (`/download`) with admin authentication, upload controls, and download analytics.

## Features

- Admin login/logout using secure session authentication.
- Protected admin routes for upload and analytics.
- Upload-only `.apk` files with file signature validation.
- Permanent download endpoint: `/download` always serves the latest uploaded APK.
- Download analytics stored in SQLite:
  - Total downloads
  - Last download timestamp
  - Recent download history (IP, referer, user agent)
- Hotlink mitigation via referer checks.
- Security headers (`nosniff`) + APK content type.
- CDN-friendly caching headers for fast delivery.
- Configurable max upload size (default 600MB).
- Gunicorn-ready for Render, Railway, VPS, DigitalOcean.

## Project Structure

```text
project/
├── app.py
├── requirements.txt
├── database.db                # auto-created at runtime
├── uploads/                   # auto-created at runtime
├── static/
│   └── styles.css
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── error.html
    ├── login.html
    ├── stats.html
    └── upload.html
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | random at startup | Flask session secret. Set explicitly in production. |
| `ADMIN_USERNAME` | `admin` | Initial admin username. |
| `ADMIN_PASSWORD` | `change-me-now` | Initial admin password (change immediately). |
| `MAX_UPLOAD_BYTES` | `629145600` | Max upload size in bytes (600MB). |
| `DOWNLOAD_CACHE_SECONDS` | `300` | Cache duration for `/download`. |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS. |
| `PORT` | `5000` | Runtime port. |

## Run (Development)

```bash
python app.py
```

## Run (Production)

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

## Usage

1. Open `/admin/login`.
2. Login with admin credentials.
3. Upload a new APK from `/admin/dashboard`.
4. Share permanent link: `/download`.
5. View analytics at `/admin/stats`.

## Deployment Notes

- Persist `uploads/` and `database.db` using platform persistent storage.
- Put app behind HTTPS and set `SESSION_COOKIE_SECURE=true`.
- For Cloudflare/CDN, keep `DOWNLOAD_CACHE_SECONDS` short if you push frequent updates.
- Replace the default admin password using environment variables before public launch.
