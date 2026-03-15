from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'data'
DASHBOARD_DIR = ROOT / 'dashboard'
DB_PATH = DATA_DIR / 'bookkeeping.sqlite'
DATA_JS_PATH = DASHBOARD_DIR / 'data.js'
CSV_GLOB = '*.csv'
PERIOD_TYPES = ('year', 'month', 'week')


@dataclass(frozen=True)
class TxnKey:
    occurred_on: str
    io_type: str
    category: str
    subcategory: str
    amount_cents: int
    memo: str


@dataclass
class SourceMeta:
    name: str
    path: str
    sha256: str
    row_count: int
    start_date: str
    end_date: str
    source_rank: int


def parse_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), '%Y年%m月%d日').date().isoformat()


def amount_to_cents(raw: str) -> int:
    value = Decimal(raw.strip()).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return int(value * 100)


def cents_to_amount(cents: int) -> float:
    return float((Decimal(cents) / Decimal('100')).quantize(Decimal('0.01')))


def split_memo_parts(memo: str) -> tuple[str, str]:
    tokens = memo.strip().split()
    tags = [token[1:] for token in tokens if token.startswith('#') and len(token) > 1]
    note_tokens = [token for token in tokens if not token.startswith('#')]
    subcategory = ' / '.join(tags)
    note = ' '.join(note_tokens).strip()
    return subcategory, note


def parse_file(path: Path, source_rank: int) -> tuple[SourceMeta, Counter[TxnKey]]:
    counts: Counter[TxnKey] = Counter()
    dates: list[str] = []

    with path.open('r', encoding='utf-16', newline='') as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            cols = [item.strip() for item in row[0].split('\t')]
            if cols[0] == '日期':
                continue
            occurred_on, io_type, category, amount_raw, memo = cols
            subcategory, note = split_memo_parts(memo)
            key = TxnKey(
                occurred_on=parse_date(occurred_on),
                io_type=io_type,
                category=category,
                subcategory=subcategory,
                amount_cents=amount_to_cents(amount_raw),
                memo=note,
            )
            counts[key] += 1
            dates.append(key.occurred_on)

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = SourceMeta(
        name=path.name,
        path=str(path),
        sha256=digest,
        row_count=sum(counts.values()),
        start_date=min(dates),
        end_date=max(dates),
        source_rank=source_rank,
    )
    return meta, counts


def build_dataset() -> tuple[list[SourceMeta], list[dict], list[dict]]:
    csv_paths = sorted(ROOT.glob(CSV_GLOB))
    source_rows: dict[str, Counter[TxnKey]] = {}
    sources: list[SourceMeta] = []

    for idx, path in enumerate(csv_paths):
        meta, counts = parse_file(path, source_rank=idx)
        sources.append(meta)
        source_rows[path.name] = counts

    max_occurrences: dict[TxnKey, int] = defaultdict(int)
    preferred_source: dict[TxnKey, SourceMeta] = {}

    for meta in sources:
        counts = source_rows[meta.name]
        for key, count in counts.items():
            if count > max_occurrences[key]:
                max_occurrences[key] = count
                preferred_source[key] = meta
            elif count == max_occurrences[key] and key in preferred_source:
                if meta.end_date > preferred_source[key].end_date:
                    preferred_source[key] = meta

    subcategory_names = sorted({key.subcategory for key in max_occurrences if key.subcategory})
    subcategories = [
        {
            'id': index + 1,
            'name': name,
            'created_from': 'memo_tag',
        }
        for index, name in enumerate(subcategory_names)
    ]
    subcategory_id_map = {item['name']: item['id'] for item in subcategories}

    transactions: list[dict] = []
    transaction_id = 1
    for key in sorted(
        max_occurrences,
        key=lambda item: (item.occurred_on, item.io_type, item.category, item.subcategory, item.amount_cents, item.memo),
    ):
        keep_count = max_occurrences[key]
        source = preferred_source[key]
        occurred = datetime.strptime(key.occurred_on, '%Y-%m-%d').date()
        iso_year, iso_week, _ = occurred.isocalendar()
        subcategory_id = subcategory_id_map.get(key.subcategory)
        week_start = occurred - timedelta(days=occurred.weekday())
        week_end = week_start + timedelta(days=6)
        for occurrence_index in range(1, keep_count + 1):
            fingerprint = hashlib.sha1(
                f'{key.occurred_on}|{key.io_type}|{key.category}|{key.subcategory}|{key.amount_cents}|{key.memo}'.encode('utf-8')
            ).hexdigest()
            transactions.append(
                {
                    'id': transaction_id,
                    'occurred_on': key.occurred_on,
                    'year': str(occurred.year),
                    'month': occurred.strftime('%Y-%m'),
                    'week': f'{iso_year}-W{iso_week:02d}',
                    'week_start': week_start.isoformat(),
                    'week_end': week_end.isoformat(),
                    'day': occurred.day,
                    'weekday': occurred.isoweekday(),
                    'io_type': key.io_type,
                    'category': key.category,
                    'subcategory_id': subcategory_id,
                    'subcategory': key.subcategory,
                    'amount_cents': key.amount_cents,
                    'amount': cents_to_amount(key.amount_cents),
                    'memo': key.memo,
                    'fingerprint': fingerprint,
                    'source_name': source.name,
                    'source_rank': source.source_rank,
                    'occurrence_index': occurrence_index,
                }
            )
            transaction_id += 1

    return sources, subcategories, transactions


def write_sqlite(sources: list[SourceMeta], subcategories: list[dict], transactions: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(
            '''
            PRAGMA journal_mode = WAL;

            CREATE TABLE source_files (
                name TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                source_rank INTEGER NOT NULL,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE subcategories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_from TEXT NOT NULL,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY,
                occurred_on TEXT NOT NULL,
                year TEXT NOT NULL,
                month TEXT NOT NULL,
                week TEXT NOT NULL,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                day INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                io_type TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory_id INTEGER,
                amount_cents INTEGER NOT NULL,
                memo TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_rank INTEGER NOT NULL,
                occurrence_index INTEGER NOT NULL,
                FOREIGN KEY (source_name) REFERENCES source_files(name),
                FOREIGN KEY (subcategory_id) REFERENCES subcategories(id)
            );

            CREATE INDEX idx_transactions_date ON transactions(occurred_on DESC);
            CREATE INDEX idx_transactions_month ON transactions(month, io_type);
            CREATE INDEX idx_transactions_week ON transactions(week, io_type);
            CREATE INDEX idx_transactions_category ON transactions(category, io_type);
            CREATE INDEX idx_transactions_subcategory_id ON transactions(subcategory_id, io_type);

            CREATE VIEW monthly_summary AS
            SELECT
                month,
                SUM(CASE WHEN io_type = '收入' THEN amount_cents ELSE 0 END) AS income_cents,
                SUM(CASE WHEN io_type = '支出' THEN amount_cents ELSE 0 END) AS expense_cents,
                COUNT(*) AS transaction_count
            FROM transactions
            GROUP BY month
            ORDER BY month DESC;
            '''
        )

        conn.executemany(
            'INSERT INTO source_files (name, path, sha256, row_count, start_date, end_date, source_rank) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(s.name, s.path, s.sha256, s.row_count, s.start_date, s.end_date, s.source_rank) for s in sources],
        )
        conn.executemany(
            'INSERT INTO subcategories (id, name, created_from) VALUES (:id, :name, :created_from)',
            subcategories,
        )
        conn.executemany(
            '''
            INSERT INTO transactions (
                id, occurred_on, year, month, week, week_start, week_end, day, weekday, io_type,
                category, subcategory_id, amount_cents, memo, fingerprint, source_name, source_rank, occurrence_index
            ) VALUES (
                :id, :occurred_on, :year, :month, :week, :week_start, :week_end, :day, :weekday, :io_type,
                :category, :subcategory_id, :amount_cents, :memo, :fingerprint, :source_name, :source_rank, :occurrence_index
            )
            ''',
            transactions,
        )
        conn.commit()
    finally:
        conn.close()


def format_period_label(period_type: str, period_key: str, context: dict | None = None) -> str:
    if period_type == 'year':
        return f'{period_key} 年'
    if period_type == 'month':
        year, month = period_key.split('-')
        return f'{year} 年 {int(month)} 月'
    if period_type == 'week':
        assert context is not None
        return f"{period_key} ({context['week_start']} - {context['week_end']})"
    raise ValueError(period_type)


def short_period_label(period_type: str, period_key: str) -> str:
    if period_type == 'year':
        return f'{period_key}年'
    if period_type == 'month':
        year, month = period_key.split('-')
        return f'{year}-{month}'
    if period_type == 'week':
        return period_key
    raise ValueError(period_type)


def child_period_label(period_type: str, child_key: str, context: dict) -> str:
    if period_type == 'year':
        return child_key.split('-')[1] + ' 月'
    if period_type == 'month':
        return f"第 {context['week_index']} 周"
    if period_type == 'week':
        return context['date']
    raise ValueError(period_type)


def rank_series(rows: Iterable[dict], io_type: str, dimension: str, limit: int = 10, skip_empty: bool = False) -> list[dict]:
    totals: Counter[str] = Counter()
    for row in rows:
        if row['io_type'] != io_type:
            continue
        name = row[dimension]
        if skip_empty and not name:
            continue
        display_name = name if name else '未标记'
        totals[display_name] += row['amount_cents']
    grand_total = sum(totals.values()) or 1
    items = []
    for name, cents in totals.most_common(limit):
        items.append(
            {
                'name': name,
                'amount': cents_to_amount(cents),
                'share': round(cents / grand_total, 4),
            }
        )
    return items


def single_expense_top(rows: Iterable[dict], limit: int = 10) -> list[dict]:
    expenses = [row for row in rows if row['io_type'] == '支出']
    expenses.sort(key=lambda item: (item['amount_cents'], item['occurred_on'], item['id']), reverse=True)
    return [
        {
            'date': row['occurred_on'],
            'category': row['category'],
            'subcategory': row['subcategory'],
            'memo': row['memo'],
            'amount': row['amount'],
        }
        for row in expenses[:limit]
    ]


def recent_transactions(rows: Iterable[dict], limit: int = 12) -> list[dict]:
    ordered = sorted(rows, key=lambda item: (item['occurred_on'], item['id']), reverse=True)
    return [
        {
            'date': row['occurred_on'],
            'io_type': row['io_type'],
            'category': row['category'],
            'subcategory': row['subcategory'],
            'memo': row['memo'],
            'amount': row['amount'],
        }
        for row in ordered[:limit]
    ]


def build_overview_rows(period_type: str, period_key: str, rows: list[dict], grouped: dict[str, list[dict]]) -> list[dict]:
    if period_type == 'year':
        keys = sorted(grouped.keys(), reverse=True)
        items = []
        for key in keys:
            rows = grouped[key]
            income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
            expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
            items.append({
                'key': key,
                'label': format_period_label('year', key),
                'income': cents_to_amount(income),
                'expense': cents_to_amount(expense),
                'balance': cents_to_amount(income - expense),
            })
        return items

    if period_type == 'month':
        target_year = period_key.split('-')[0]
        keys = sorted([key for key in grouped.keys() if key.startswith(target_year + '-')], reverse=True)
        items = []
        for key in keys:
            rows = grouped[key]
            income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
            expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
            items.append({
                'key': key,
                'label': format_period_label('month', key),
                'income': cents_to_amount(income),
                'expense': cents_to_amount(expense),
                'balance': cents_to_amount(income - expense),
            })
        return items

    target_month = rows[0]['month'] if rows else ''
    keys = sorted([key for key, rows in grouped.items() if rows and rows[0]['month'] == target_month], reverse=True)
    items = []
    for key in keys:
        rows = grouped[key]
        income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
        expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
        items.append({
            'key': key,
            'label': format_period_label('week', key, {'week_start': rows[0]['week_start'], 'week_end': rows[0]['week_end']}),
            'income': cents_to_amount(income),
            'expense': cents_to_amount(expense),
            'balance': cents_to_amount(income - expense),
        })
    return items


def build_period_snapshot(period_type: str, period_key: str, rows: list[dict], grouped: dict[str, dict[str, list[dict]]]) -> dict:
    expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
    income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
    active_days = len({row['occurred_on'] for row in rows if row['io_type'] == '支出'}) or 1
    context = None
    if period_type == 'week' and rows:
        context = {'week_start': rows[0]['week_start'], 'week_end': rows[0]['week_end']}

    return {
        'key': period_key,
        'label': format_period_label(period_type, period_key, context),
        'income': cents_to_amount(income),
        'expense': cents_to_amount(expense),
        'balance': cents_to_amount(income - expense),
        'daily_avg_expense': round(expense / 100 / active_days, 2),
        'transactions': len(rows),
        'active_days': active_days,
        'rankings': {
            'primary': rank_series(rows, '支出', 'category'),
            'secondary': rank_series(rows, '支出', 'subcategory', skip_empty=True),
        },
        'single_expense_top10': single_expense_top(rows),
        'recent_transactions': recent_transactions(rows),
        'overview_rows': build_overview_rows(period_type, period_key, rows, grouped[period_type]),
    }


def build_trend_series(period_type: str, grouped_rows: dict[str, list[dict]], ordered_keys: list[str]) -> list[dict]:
    limit = 8 if period_type == 'year' else 12
    selected_keys = ordered_keys[-limit:]
    trend = []
    for key in selected_keys:
        rows = grouped_rows[key]
        income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
        expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
        context = None
        if period_type == 'week':
            context = {'week_start': rows[0]['week_start'], 'week_end': rows[0]['week_end']}
        trend.append(
            {
                'key': key,
                'label': format_period_label(period_type, key, context),
                'short_label': key if period_type == 'year' else (key[-2:] if period_type == 'month' else key.split('-W')[1]),
                'income': cents_to_amount(income),
                'expense': cents_to_amount(expense),
            }
        )
    return trend


def build_dashboard_payload(sources: list[SourceMeta], subcategories: list[dict], transactions: list[dict]) -> dict:
    grouped: dict[str, dict[str, list[dict]]] = {period_type: defaultdict(list) for period_type in PERIOD_TYPES}
    for row in transactions:
        grouped['year'][row['year']].append(row)
        grouped['month'][row['month']].append(row)
        grouped['week'][row['week']].append(row)

    views = {}
    controls = {'period_types': list(PERIOD_TYPES), 'options': {}, 'default_periods': {}}
    default_period_type = 'month'

    for period_type in PERIOD_TYPES:
        ordered_keys = sorted(grouped[period_type].keys())
        default_key = ordered_keys[-1]
        controls['options'][period_type] = [
            {
                'value': key,
                'label': short_period_label(period_type, key),
            }
            for key in reversed(ordered_keys)
        ]
        controls['default_periods'][period_type] = default_key
        views[period_type] = {
            'trend': build_trend_series(period_type, grouped[period_type], ordered_keys),
            'periods': {key: build_period_snapshot(period_type, key, grouped[period_type][key], grouped) for key in ordered_keys},
        }

    summary = {
        'source_files': len(sources),
        'raw_rows': sum(source.row_count for source in sources),
        'unique_transactions': len(transactions),
        'duplicates_removed': sum(source.row_count for source in sources) - len(transactions),
        'subcategory_count': len(subcategories),
        'date_range': {
            'start': min(txn['occurred_on'] for txn in transactions),
            'end': max(txn['occurred_on'] for txn in transactions),
        },
    }

    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'summary': summary,
        'controls': controls,
        'default_period_type': default_period_type,
        'views': views,
        'detail': build_detail_payload(transactions),
    }


def weekday_label(iso_date: str) -> str:
    labels = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    dt = datetime.strptime(iso_date, '%Y-%m-%d').date()
    return labels[dt.weekday()]


def build_detail_payload(transactions: list[dict]) -> dict:
    months = sorted({row['month'] for row in transactions}, reverse=True)
    years = sorted({row['year'] for row in transactions}, reverse=True)
    month_groups = {}
    for month in months:
        rows = [row for row in transactions if row['month'] == month]
        income = sum(row['amount_cents'] for row in rows if row['io_type'] == '收入')
        expense = sum(row['amount_cents'] for row in rows if row['io_type'] == '支出')
        by_day = defaultdict(list)
        for row in sorted(rows, key=lambda item: (item['occurred_on'], item['id']), reverse=True):
            by_day[row['occurred_on']].append({
                'id': row['id'],
                'io_type': row['io_type'],
                'category': row['category'],
                'subcategory': row['subcategory'],
                'memo': row['memo'],
                'amount': row['amount'],
            })
        days = []
        for day in sorted(by_day.keys(), reverse=True):
            items = by_day[day]
            day_expense = round(sum(item['amount'] for item in items if item['io_type'] == '支出'), 2)
            day_income = round(sum(item['amount'] for item in items if item['io_type'] == '收入'), 2)
            days.append({
                'date': day,
                'weekday': weekday_label(day),
                'expense': day_expense,
                'income': day_income,
                'items': items,
            })
        month_groups[month] = {
            'month': month,
            'label': format_period_label('month', month),
            'income': cents_to_amount(income),
            'expense': cents_to_amount(expense),
            'days': days,
        }
    year_months = {year: [month for month in months if month.startswith(year + '-')] for year in years}
    return {
        'years': years,
        'year_months': year_months,
        'default_year': years[0],
        'default_month': months[0],
        'months': month_groups,
    }


def write_dashboard_data(payload: dict) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_JS_PATH.write_text(
        'window.BOOKKEEPING_DATA = ' + json.dumps(payload, ensure_ascii=False, indent=2) + ';\n',
        encoding='utf-8',
    )


def main() -> None:
    sources, subcategories, transactions = build_dataset()
    write_sqlite(sources, subcategories, transactions)
    payload = build_dashboard_payload(sources, subcategories, transactions)
    write_dashboard_data(payload)
    print(f'Wrote {DB_PATH}')
    print(f'Wrote {DATA_JS_PATH}')
    print(f'Subcategories kept: {len(subcategories)}')
    print(f'Transactions kept: {len(transactions)}')
    print(f"Date range: {payload['summary']['date_range']['start']} -> {payload['summary']['date_range']['end']}")


if __name__ == '__main__':
    main()
