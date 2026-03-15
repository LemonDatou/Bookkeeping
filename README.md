# Bookkeeping Service

## Start

```bash
cd Bookkeeping
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOOKKEEPING_ADMIN_PASSWORD='<set-a-strong-password>'
export BOOKKEEPING_INGEST_TOKEN='<set-a-long-random-token>'
export BOOKKEEPING_SECRET_KEY='<set-a-long-random-session-secret>'
python3 server/app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Env

- `BOOKKEEPING_ADMIN_PASSWORD`: plain text admin password for single-user login
- `BOOKKEEPING_ADMIN_PASSWORD_HASH`: optional werkzeug password hash; preferred over plain text when set
- `BOOKKEEPING_INGEST_TOKEN`: machine token for SMS/automation ingestion API
- `BOOKKEEPING_INGEST_TOKEN_HASH`: optional werkzeug hash for the ingestion token
- `BOOKKEEPING_SECRET_KEY`: session signing key, required
- `BOOKKEEPING_SECURE_COOKIE`: set `1` behind HTTPS in production
- `BOOKKEEPING_TIMEZONE`: backup timezone, default `Asia/Shanghai`
- `PORT`: service port, default `8000`

## Notes

- All API writes require login session + CSRF token.
- SMS/automation ingestion uses `Authorization: Bearer <token>` or `X-Bookkeeping-Token: <token>`.
- Database backups are written daily at 00:00 to `data/backups/`.
- The initial SQLite file is `data/bookkeeping.sqlite`.
- The service will refuse to start unless admin password, ingest token, and session secret are explicitly provided.

## SMS Ingest API

`POST /api/ingest/sms`

Headers:

- `Authorization: Bearer <BOOKKEEPING_INGEST_TOKEN>`
- `Content-Type: application/json`

Body:

```json
{
  "content": "银行短信"
}
```

Notes:

- You can also send `text/plain` directly in the request body without JSON wrapping
- Required input: only the raw SMS content
- The server currently stores the SMS into `sms_inbox` with status `pending`
- If you send the exact same SMS content again, the API will return the existing inbox record instead of duplicating it
