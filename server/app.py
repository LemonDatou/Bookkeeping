from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

from payloads import DB_PATH, ROOT, amount_to_cents, build_dashboard_payload, build_dashboard_skeleton, build_detail_bootstrap, build_month_detail, build_single_snapshot

APP_TZ = ZoneInfo(os.environ.get('BOOKKEEPING_TIMEZONE', 'Asia/Shanghai'))
BACKUP_DIR = ROOT / 'data' / 'backups'
DASHBOARD_DIR = ROOT / 'dashboard'
MANUAL_SOURCE = 'manual'
LOGIN_TEMPLATE = '''<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>记账登录</title>
    <style>
      body { margin:0; font-family:"SF Pro Display","PingFang SC",sans-serif; background:linear-gradient(180deg,#fbf6ee,#f3ede3); color:#1f2937; }
      .shell { min-height:100vh; display:grid; place-items:center; padding:24px; }
      .card { width:min(100%,420px); background:rgba(255,255,255,.9); border-radius:24px; padding:28px; box-shadow:0 18px 60px rgba(31,41,55,.12); }
      h1 { margin:0 0 10px; font-size:32px; }
      p { margin:0 0 18px; color:#6b7280; line-height:1.6; }
      label { display:grid; gap:8px; font-size:14px; color:#6b7280; }
      input { border:1px solid rgba(31,41,55,.1); border-radius:14px; padding:14px 16px; font-size:16px; }
      button { width:100%; margin-top:16px; border:0; border-radius:14px; padding:14px 16px; font-size:16px; font-weight:700; color:white; background:linear-gradient(135deg,#ff9d72,#ff6b57); }
      .error { margin-top:12px; color:#b42318; font-size:14px; }
    </style>
  </head>
  <body>
    <div class="shell">
      <form class="card" method="post">
        <h1>记账服务</h1>
        <p>仅允许授权用户访问。请输入你的登录密码。</p>
        <label>
          <span>密码</span>
          <input name="password" type="password" autocomplete="current-password" required />
        </label>
        <button type="submit">登录</button>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
      </form>
    </div>
  </body>
</html>'''


def create_app() -> Flask:
    app = Flask(__name__)
    app.config['SECRET_KEY'] = required_env('BOOKKEEPING_SECRET_KEY')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('BOOKKEEPING_SECURE_COOKIE', '').lower() in {'1', 'true', 'yes'}
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    assert_required_secrets()
    ensure_manual_source(DB_PATH)
    ensure_ingest_tables(DB_PATH)
    start_backup_scheduler(DB_PATH)

    def is_logged_in() -> bool:
        return bool(session.get('authenticated'))

    def wants_json() -> bool:
        return request.path.startswith('/api/')

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not is_logged_in():
                if wants_json():
                    return jsonify({'error': 'unauthorized'}), 401
                return redirect(url_for('login', next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def require_csrf() -> Response | None:
        token = request.headers.get('X-CSRF-Token', '')
        if not token or token != session.get('csrf_token'):
            return jsonify({'error': 'invalid_csrf'}), 403
        return None

    def require_ingest_auth() -> Response | None:
        token = request.headers.get('X-Bookkeeping-Token', '')
        auth = request.headers.get('Authorization', '')
        if not token and auth.startswith('Bearer '):
            token = auth[7:].strip()
        if not token or not verify_ingest_token(token):
            return jsonify({'error': 'unauthorized'}), 401
        return None

    @app.get('/healthz')
    def healthz():
        return {'ok': True, 'time': datetime.now(APP_TZ).isoformat()}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if is_logged_in():
            return redirect(url_for('index'))
        error = ''
        if request.method == 'POST':
            password = request.form.get('password', '')
            if verify_password(password):
                session.clear()
                session.permanent = True
                session['authenticated'] = True
                session['csrf_token'] = secrets.token_urlsafe(24)
                return redirect(request.args.get('next') or url_for('index'))
            time.sleep(0.8)
            error = '密码错误'
        return render_template_string(LOGIN_TEMPLATE, error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @login_required
    def index():
        return send_from_directory(DASHBOARD_DIR, 'index.html')

    @app.get('/detail.html')
    @login_required
    def detail_page():
        return send_from_directory(DASHBOARD_DIR, 'detail.html')

    @app.get('/<path:filename>')
    @login_required
    def static_files(filename: str):
        if filename in {'app.js', 'styles.css', 'detail.js', 'detail.css'}:
            return send_from_directory(DASHBOARD_DIR, filename)
        return ('Not Found', 404)

    @app.get('/api/session')
    @login_required
    def api_session():
        return {'csrf_token': session['csrf_token']}

    @app.get('/api/dashboard')
    @login_required
    def api_dashboard():
        payload = build_dashboard_payload(DB_PATH)
        payload['csrf_token'] = session['csrf_token']
        return payload


    @app.get("/api/dashboard/init")
    @login_required
    def api_dashboard_init():
        payload = build_dashboard_skeleton(DB_PATH)
        payload["csrf_token"] = session["csrf_token"]
        return payload

    @app.get("/api/dashboard/snapshot")
    @login_required
    def api_dashboard_snapshot():
        period_type = request.args.get("type", "")
        period_key = request.args.get("key", "")
        if not period_type or not period_key:
            return jsonify({"error": "missing type or key"}), 400
        return build_single_snapshot(period_type, period_key, DB_PATH)
    @app.get('/api/detail/bootstrap')
    @login_required
    def api_detail_bootstrap():
        payload = build_detail_bootstrap(DB_PATH)
        payload['csrf_token'] = session['csrf_token']
        return payload

    @app.get('/api/detail/months/<month>')
    @login_required
    def api_detail_month(month: str):
        return build_month_detail(month, DB_PATH)

    @app.post('/api/transactions')
    @login_required
    def api_create_transaction():
        if (resp := require_csrf()) is not None:
            return resp
        payload = request.get_json(force=True, silent=True) or {}
        row = insert_transaction(DB_PATH, payload)
        return jsonify(row), 201

    @app.patch('/api/transactions/<int:transaction_id>')
    @login_required
    def api_update_transaction(transaction_id: int):
        if (resp := require_csrf()) is not None:
            return resp
        payload = request.get_json(force=True, silent=True) or {}
        conn = sqlite3.connect(DB_PATH)
        try:
            old = fetch_transaction_by_id(conn, transaction_id)
        finally:
            conn.close()
        row = update_transaction(DB_PATH, transaction_id, payload)
        if old['category'] == '未知' and row['category'] != '未知':
            merchant = _merchant_for_transaction(DB_PATH, transaction_id, old['memo'])
            if merchant:
                upsert_merchant_category(DB_PATH, merchant, row['category'], row['subcategory'])
        return jsonify(row)

    @app.delete('/api/transactions/<int:transaction_id>')
    @login_required
    def api_delete_transaction(transaction_id: int):
        if (resp := require_csrf()) is not None:
            return resp
        delete_transaction(DB_PATH, transaction_id)
        return '', 204

    @app.post('/api/ingest/sms')
    def api_ingest_sms():
        if (resp := require_ingest_auth()) is not None:
            return resp
        payload = request.get_json(silent=True)
        sms_text = extract_sms_text(payload, request)
        row, status_code = ingest_sms(DB_PATH, sms_text)
        return jsonify(row), status_code

    @app.errorhandler(ValueError)
    def handle_value_error(error: ValueError):
        if wants_json():
            return jsonify({'error': str(error)}), 400
        return str(error), 400

    return app


def required_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'Missing required environment variable: {name}')
    return value


def assert_required_secrets() -> None:
    if not (os.environ.get('BOOKKEEPING_ADMIN_PASSWORD', '').strip() or os.environ.get('BOOKKEEPING_ADMIN_PASSWORD_HASH', '').strip()):
        raise RuntimeError('Missing admin credential. Set BOOKKEEPING_ADMIN_PASSWORD or BOOKKEEPING_ADMIN_PASSWORD_HASH.')
    if not (os.environ.get('BOOKKEEPING_INGEST_TOKEN', '').strip() or os.environ.get('BOOKKEEPING_INGEST_TOKEN_HASH', '').strip()):
        raise RuntimeError('Missing ingest credential. Set BOOKKEEPING_INGEST_TOKEN or BOOKKEEPING_INGEST_TOKEN_HASH.')


def verify_password(password: str) -> bool:
    return verify_secret(
        secret=password,
        plain_env='BOOKKEEPING_ADMIN_PASSWORD',
        hash_env='BOOKKEEPING_ADMIN_PASSWORD_HASH',
        required_label='admin password',
    )


def verify_ingest_token(token: str) -> bool:
    return verify_secret(
        secret=token,
        plain_env='BOOKKEEPING_INGEST_TOKEN',
        hash_env='BOOKKEEPING_INGEST_TOKEN_HASH',
        required_label='ingest token',
    )


def verify_secret(secret: str, plain_env: str, hash_env: str, required_label: str) -> bool:
    password_hash = os.environ.get(hash_env, '')
    plain = os.environ.get(plain_env, '')
    if password_hash:
        return check_password_hash(password_hash, secret)
    if plain:
        return secrets.compare_digest(plain, secret)
    raise RuntimeError(f'Set {plain_env} or {hash_env} before starting the server to enable {required_label}.')


def compute_time_fields(occurred_on: str) -> dict[str, Any]:
    dt = datetime.strptime(occurred_on, '%Y-%m-%d').date()
    iso_year, iso_week, _ = dt.isocalendar()
    week_start = dt - timedelta(days=dt.weekday())
    week_end = week_start + timedelta(days=6)
    return {
        'year': str(dt.year),
        'month': dt.strftime('%Y-%m'),
        'week': f'{iso_year}-W{iso_week:02d}',
        'week_start': week_start.isoformat(),
        'week_end': week_end.isoformat(),
        'day': dt.day,
        'weekday': dt.isoweekday(),
    }


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    io_type = str(payload.get('io_type', '')).strip()
    if io_type not in {'收入', '支出'}:
        raise ValueError('`io_type` 必须是 收入 或 支出')
    occurred_on = str(payload.get('occurred_on', '')).strip()
    datetime.strptime(occurred_on, '%Y-%m-%d')
    category = str(payload.get('category', '')).strip()
    if not category:
        raise ValueError('`category` 为必填')
    amount_raw = payload.get('amount')
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, TypeError):
        raise ValueError('`amount` 必须是合法数字') from None
    if amount <= 0:
        raise ValueError('`amount` 必须大于 0')
    subcategory = str(payload.get('subcategory', '') or '').strip()
    memo = str(payload.get('memo', '') or '').strip()
    return {
        'io_type': io_type,
        'occurred_on': occurred_on,
        'category': category,
        'amount_cents': amount_to_cents(amount),
        'subcategory': subcategory,
        'memo': memo,
    }


def transaction_fingerprint(row: dict[str, Any]) -> str:
    return hashlib.sha1(
        f"{row['occurred_on']}|{row['io_type']}|{row['category']}|{row['subcategory']}|{row['amount_cents']}|{row['memo']}".encode('utf-8')
    ).hexdigest()


def ensure_manual_source(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            '''
            INSERT INTO source_files (name, path, sha256, row_count, start_date, end_date, source_rank)
            VALUES (?, ?, ?, 0, date('now'), date('now'), 9999)
            ON CONFLICT(name) DO NOTHING
            ''',
            (MANUAL_SOURCE, str(db_path), 'manual'),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_ingest_tables(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS ingest_requests (
              external_ref TEXT PRIMARY KEY,
              transaction_id INTEGER NOT NULL,
              source_channel TEXT NOT NULL DEFAULT 'sms',
              source_text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS sms_inbox (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              content TEXT NOT NULL,
              content_hash TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              parsed_at TEXT,
              transaction_id INTEGER,
              parser_note TEXT NOT NULL DEFAULT '',
              FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS merchant_categories (
              merchant TEXT PRIMARY KEY,
              category TEXT NOT NULL DEFAULT '未知',
              subcategory TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        conn.commit()
    finally:
        conn.close()


def ensure_subcategory(conn: sqlite3.Connection, name: str) -> int | None:
    if not name:
        return None
    row = conn.execute('SELECT id FROM subcategories WHERE name = ?', (name,)).fetchone()
    if row:
        return row[0]
    cursor = conn.execute('INSERT INTO subcategories (name, created_from) VALUES (?, ?)', (name, 'manual'))
    return int(cursor.lastrowid)


def fetch_transaction_by_id(conn: sqlite3.Connection, transaction_id: int) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        '''
        SELECT t.id, t.occurred_on, t.io_type, t.category, COALESCE(s.name, '') AS subcategory,
               t.memo, t.amount_cents
        FROM transactions t
        LEFT JOIN subcategories s ON s.id = t.subcategory_id
        WHERE t.id = ?
        ''',
        (transaction_id,),
    ).fetchone()
    if not row:
        raise ValueError('交易不存在')
    return {
        'id': row['id'],
        'occurred_on': row['occurred_on'],
        'io_type': row['io_type'],
        'category': row['category'],
        'subcategory': row['subcategory'],
        'memo': row['memo'],
        'amount': float(Decimal(row['amount_cents']) / Decimal('100')),
    }


def insert_transaction(db_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_payload(payload)
    derived = compute_time_fields(normalized['occurred_on'])
    normalized.update(derived)
    normalized['fingerprint'] = transaction_fingerprint(normalized)
    conn = sqlite3.connect(db_path)
    try:
        subcategory_id = ensure_subcategory(conn, normalized['subcategory'])
        cursor = conn.execute(
            '''
            INSERT INTO transactions (
              occurred_on, year, month, week, week_start, week_end, day, weekday, io_type, category,
              subcategory_id, amount_cents, memo, fingerprint, source_name, source_rank, occurrence_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                normalized['occurred_on'], normalized['year'], normalized['month'], normalized['week'], normalized['week_start'], normalized['week_end'],
                normalized['day'], normalized['weekday'], normalized['io_type'], normalized['category'], subcategory_id,
                normalized['amount_cents'], normalized['memo'], normalized['fingerprint'], MANUAL_SOURCE, 9999, 1,
            ),
        )
        conn.commit()
        return fetch_transaction_by_id(conn, int(cursor.lastrowid))
    finally:
        conn.close()


def update_transaction(db_path: Path, transaction_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_payload(payload)
    derived = compute_time_fields(normalized['occurred_on'])
    normalized.update(derived)
    normalized['fingerprint'] = transaction_fingerprint(normalized)
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute('SELECT 1 FROM transactions WHERE id = ?', (transaction_id,)).fetchone()
        if not exists:
            raise ValueError('交易不存在')
        subcategory_id = ensure_subcategory(conn, normalized['subcategory'])
        conn.execute(
            '''
            UPDATE transactions
            SET occurred_on = ?, year = ?, month = ?, week = ?, week_start = ?, week_end = ?, day = ?, weekday = ?,
                io_type = ?, category = ?, subcategory_id = ?, amount_cents = ?, memo = ?, fingerprint = ?,
                source_name = ?, source_rank = ?, occurrence_index = ?
            WHERE id = ?
            ''',
            (
                normalized['occurred_on'], normalized['year'], normalized['month'], normalized['week'], normalized['week_start'], normalized['week_end'],
                normalized['day'], normalized['weekday'], normalized['io_type'], normalized['category'], subcategory_id,
                normalized['amount_cents'], normalized['memo'], normalized['fingerprint'], MANUAL_SOURCE, 9999, 1, transaction_id,
            ),
        )
        conn.commit()
        return fetch_transaction_by_id(conn, transaction_id)
    finally:
        conn.close()


def delete_transaction(db_path: Path, transaction_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise ValueError('交易不存在')
    finally:
        conn.close()


def extract_sms_text(payload: Any, req: request.__class__) -> str:
    if isinstance(payload, dict):
        sms_text = str(payload.get('content', '') or payload.get('sms', '') or payload.get('text', '')).strip()
        if sms_text:
            return sms_text
    raw_text = req.get_data(as_text=True).strip()
    if raw_text:
        return raw_text
    raise ValueError('短信内容不能为空')


def _extract_amount(text: str) -> Decimal | None:
    for pattern in [
        r'[¥￥](\d+(?:\.\d+)?)',
        r'人民币(\d+(?:\.\d+)?)',
        r'(?:消费|支付|付款|到账)[^\d]{0,5}(\d+(?:\.\d+)?)元',
    ]:
        m = re.search(pattern, text)
        if m:
            try:
                v = Decimal(m.group(1))
                if v > 0:
                    return v
            except InvalidOperation:
                continue
    return None


def _extract_date(text: str, now: datetime) -> str | None:
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year if month <= now.month else now.year - 1
        return f"{year}-{month:02d}-{day:02d}"
    return None


def parse_sms(sms_text: str) -> dict[str, Any] | None:
    """解析中文银行/支付短信，返回解析结果 dict，无法识别时返回 None。"""
    now = datetime.now(APP_TZ)
    time_m = re.search(r'(\d{2}:\d{2})', sms_text)
    time_str = time_m.group(1) if time_m else ''

    # 扣收费用类（短信费等）
    m = re.search(r'扣收(.{1,30}?)人民币(\d+(?:\.\d+)?)', sms_text)
    if m:
        date_str = _extract_date(sms_text, now)
        if not date_str:
            return None
        description = m.group(1).strip()
        norm = re.sub(r'^\d{1,2}月', '', description)
        result: dict[str, Any] = {
            'date': date_str, 'time': time_str,
            'amount': Decimal(m.group(2)), 'merchant': description, 'io_type': '支出',
        }
        if '短信费' in norm:
            result['category_override'] = '日用'
            result['subcategory_override'] = '月租费'
        return result

    amount = _extract_amount(sms_text)
    if not amount:
        return None
    date_str = _extract_date(sms_text, now)
    if not date_str:
        return None

    # 快捷支付：在X快捷支付，商家名取全段
    m = re.search(r'在(.+?)快捷支付', sms_text)
    if m:
        return {'date': date_str, 'time': time_str, 'amount': amount, 'merchant': m.group(1).strip(), 'io_type': '支出'}

    # 在【X】消费
    m = re.search(r'在【([^】]+)】', sms_text)
    if m:
        return {'date': date_str, 'time': time_str, 'amount': amount, 'merchant': m.group(1).strip(), 'io_type': '支出'}

    # 引号包裹
    m = re.search(r'[""「]([^""」]+)[""」]', sms_text)
    if m:
        return {'date': date_str, 'time': time_str, 'amount': amount, 'merchant': m.group(1).strip(), 'io_type': '支出'}

    return None


def _merchant_for_transaction(db_path: Path, transaction_id: int, fallback_memo: str) -> str:
    """从 sms_inbox.parser_note 取商家名（格式：merchant=X）。
    parser_note 是入账时写入的原始值，不受备注编辑影响。
    若找不到则降级：剥掉备注末尾的 HH:MM。"""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT parser_note FROM sms_inbox WHERE transaction_id = ? AND parser_note LIKE 'merchant=%' LIMIT 1",
            (transaction_id,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row[0][len('merchant='):]
    return re.sub(r'\s+\d{2}:\d{2}$', '', fallback_memo.strip())


def upsert_merchant_category(db_path: Path, merchant: str, category: str, subcategory: str) -> None:
    if not merchant or not category or category == '未知':
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            '''
            INSERT INTO merchant_categories (merchant, category, subcategory, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(merchant) DO UPDATE SET
              category = excluded.category,
              subcategory = excluded.subcategory,
              updated_at = CURRENT_TIMESTAMP
            ''',
            (merchant, category, subcategory),
        )
        conn.commit()
    finally:
        conn.close()


def ingest_sms(db_path: Path, sms_text: str) -> tuple[dict[str, Any], int]:
    content_hash = hashlib.sha1(sms_text.encode('utf-8')).hexdigest()

    # 1. 去重检查 + 商家分类查询
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute(
            'SELECT id, content, status, created_at, parsed_at, transaction_id, parser_note FROM sms_inbox WHERE content_hash = ?',
            (content_hash,),
        ).fetchone()
        if existing:
            return {
                'id': existing['id'],
                'content': existing['content'],
                'status': existing['status'],
                'created_at': existing['created_at'],
                'parsed_at': existing['parsed_at'],
                'transaction_id': existing['transaction_id'],
                'parser_note': existing['parser_note'],
                'deduplicated': True,
            }, 200

        parsed = parse_sms(sms_text)
        category = '未知'
        subcategory = ''
        if parsed:
            if parsed.get('category_override'):
                category = parsed['category_override']
                subcategory = parsed.get('subcategory_override', '')
            else:
                cat_row = conn.execute(
                    'SELECT category, subcategory FROM merchant_categories WHERE merchant = ?',
                    (parsed['merchant'],),
                ).fetchone()
                if cat_row:
                    category = cat_row['category']
                    subcategory = cat_row['subcategory']
    finally:
        conn.close()

    # 2. 创建交易记录（或记录解析失败）
    transaction: dict[str, Any] | None = None
    if parsed:
        memo = f"{parsed['merchant']} {parsed['time']}" if parsed['time'] else parsed['merchant']
        try:
            transaction = insert_transaction(db_path, {
                'io_type': parsed['io_type'],
                'occurred_on': parsed['date'],
                'amount': parsed['amount'],
                'category': category,
                'subcategory': subcategory,
                'memo': memo,
            })
            status = 'parsed'
            parser_note = f"merchant={parsed['merchant']}"
        except Exception as e:
            status = 'error'
            parser_note = f"创建交易失败：{e}"
    else:
        status = 'error'
        parser_note = '解析失败：无法识别短信格式'

    # 3. 写入 sms_inbox
    now_str = datetime.now(APP_TZ).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            'INSERT INTO sms_inbox (content, content_hash, status, parsed_at, transaction_id, parser_note) VALUES (?, ?, ?, ?, ?, ?)',
            (
                sms_text, content_hash, status,
                now_str if status == 'parsed' else None,
                transaction['id'] if transaction else None,
                parser_note,
            ),
        )
        conn.commit()
        row = conn.execute(
            'SELECT id, content, status, created_at, parsed_at, transaction_id, parser_note FROM sms_inbox WHERE id = ?',
            (cursor.lastrowid,),
        ).fetchone()
        result: dict[str, Any] = {
            'id': row['id'],
            'content': row['content'],
            'status': row['status'],
            'created_at': row['created_at'],
            'parsed_at': row['parsed_at'],
            'transaction_id': row['transaction_id'],
            'parser_note': row['parser_note'],
            'deduplicated': False,
        }
        if transaction:
            result['transaction'] = transaction
        return result, 201
    finally:
        conn.close()


def ingest_transaction(db_path: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    external_ref = str(payload.get('external_ref', '') or '').strip()
    source_channel = str(payload.get('source_channel', '') or 'sms').strip() or 'sms'
    source_text = str(payload.get('source_text', '') or '').strip()

    conn = sqlite3.connect(db_path)
    try:
        if external_ref:
            row = conn.execute('SELECT transaction_id FROM ingest_requests WHERE external_ref = ?', (external_ref,)).fetchone()
            if row:
                existing = fetch_transaction_by_id(conn, int(row[0]))
                existing['ingested'] = False
                existing['external_ref'] = external_ref
                return existing, 200
    finally:
        conn.close()

    created = insert_transaction(
        db_path,
        {
            'io_type': payload.get('io_type'),
            'occurred_on': payload.get('occurred_on'),
            'amount': payload.get('amount'),
            'category': payload.get('category'),
            'subcategory': payload.get('subcategory', ''),
            'memo': payload.get('memo', ''),
        },
    )
    if external_ref:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                '''
                INSERT OR IGNORE INTO ingest_requests (external_ref, transaction_id, source_channel, source_text)
                VALUES (?, ?, ?, ?)
                ''',
                (external_ref, created['id'], source_channel, source_text),
            )
            conn.commit()
        finally:
            conn.close()
    created['ingested'] = True
    if external_ref:
        created['external_ref'] = external_ref
    return created, 201


def backup_database(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(APP_TZ).strftime('%Y-%m-%d')
    target = BACKUP_DIR / f'bookkeeping-{stamp}.sqlite'
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return target


def backup_loop(db_path: Path) -> None:
    ensure_backup_today(db_path)
    while True:
        now = datetime.now(APP_TZ)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_seconds = max((next_run - now).total_seconds(), 30)
        time.sleep(sleep_seconds)
        ensure_backup_today(db_path)


def ensure_backup_today(db_path: Path) -> None:
    stamp = datetime.now(APP_TZ).strftime('%Y-%m-%d')
    target = BACKUP_DIR / f'bookkeeping-{stamp}.sqlite'
    if not target.exists():
        backup_database(db_path)


def start_backup_scheduler(db_path: Path) -> None:
    thread = threading.Thread(target=backup_loop, args=(db_path,), daemon=True, name='bookkeeping-backup')
    thread.start()


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '8000')), debug=False)
