from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'bookkeeping.sqlite'
PERIOD_TYPES = ('year', 'month', 'week')


def cents_to_amount(cents: int) -> float:
    return float((Decimal(cents) / Decimal('100')).quantize(Decimal('0.01')))


def amount_to_cents(amount: str | float | int) -> int:
    return int((Decimal(str(amount)).quantize(Decimal('0.01'))) * 100)


def weekday_label(iso_date: str) -> str:
    labels = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    dt = datetime.strptime(iso_date, '%Y-%m-%d').date()
    return labels[dt.weekday()]


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


def fetch_transactions(db_path: Path = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT
              t.id,
              t.occurred_on,
              t.year,
              t.month,
              t.week,
              t.week_start,
              t.week_end,
              t.day,
              t.weekday,
              t.io_type,
              t.category,
              COALESCE(s.name, '') AS subcategory,
              t.subcategory_id,
              t.amount_cents,
              t.memo,
              t.fingerprint,
              t.source_name,
              t.source_rank,
              t.occurrence_index
            FROM transactions t
            LEFT JOIN subcategories s ON s.id = t.subcategory_id
            ORDER BY t.occurred_on, t.id
            '''
        ).fetchall()
        return [
            {
                **dict(row),
                'amount': cents_to_amount(row['amount_cents']),
            }
            for row in rows
        ]
    finally:
        conn.close()


def fetch_summary(db_path: Path = DB_PATH) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        source_files = conn.execute('SELECT COUNT(*) FROM source_files').fetchone()[0]
        raw_rows = conn.execute('SELECT COALESCE(SUM(row_count), 0) FROM source_files').fetchone()[0]
        unique_transactions = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        subcategory_count = conn.execute('SELECT COUNT(*) FROM subcategories').fetchone()[0]
        date_range = conn.execute('SELECT MIN(occurred_on), MAX(occurred_on) FROM transactions').fetchone()
        return {
            'source_files': source_files,
            'raw_rows': raw_rows,
            'unique_transactions': unique_transactions,
            'duplicates_removed': raw_rows - unique_transactions,
            'subcategory_count': subcategory_count,
            'date_range': {
                'start': date_range[0],
                'end': date_range[1],
            },
        }
    finally:
        conn.close()


def rank_series(rows: Iterable[dict], io_type: str, dimension: str, limit: int = 10, skip_empty: bool = False) -> list[dict]:
    totals: Counter[str] = Counter()
    for row in rows:
        if row['io_type'] != io_type:
            continue
        name = row[dimension]
        if skip_empty and not name:
            continue
        totals[name if name else '未标记'] += row['amount_cents']
    grand_total = sum(totals.values()) or 1
    return [
        {
            'name': name,
            'amount': cents_to_amount(cents),
            'share': round(cents / grand_total, 4),
        }
        for name, cents in totals.most_common(limit)
    ]


def single_expense_top(rows: Iterable[dict], limit: int = 10) -> list[dict]:
    expenses = [row for row in rows if row['io_type'] == '支出']
    expenses.sort(key=lambda item: (item['amount_cents'], item['occurred_on'], item['id']), reverse=True)
    return [
        {
            'id': row['id'],
            'date': row['occurred_on'],
            'category': row['category'],
            'subcategory': row['subcategory'],
            'memo': row['memo'],
            'amount': row['amount'],
        }
        for row in expenses[:limit]
    ]


def build_overview_rows(period_type: str, period_key: str, rows: list[dict], grouped: dict[str, list[dict]]) -> list[dict]:
    if period_type == 'year':
        keys = sorted(grouped.keys(), reverse=True)
    elif period_type == 'month':
        year = period_key.split('-')[0]
        keys = sorted([key for key in grouped if key.startswith(year + '-')], reverse=True)
    else:
        target_month = rows[0]['month'] if rows else ''
        keys = sorted([key for key, value in grouped.items() if value and value[0]['month'] == target_month], reverse=True)

    result = []
    for key in keys:
        items = grouped[key]
        income = sum(row['amount_cents'] for row in items if row['io_type'] == '收入')
        expense = sum(row['amount_cents'] for row in items if row['io_type'] == '支出')
        if period_type == 'week':
            label = format_period_label('week', key, {'week_start': items[0]['week_start'], 'week_end': items[0]['week_end']})
        else:
            label = format_period_label(period_type, key)
        result.append(
            {
                'key': key,
                'label': label,
                'income': cents_to_amount(income),
                'expense': cents_to_amount(expense),
                'balance': cents_to_amount(income - expense),
            }
        )
    return result


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
        trend.append(
            {
                'key': key,
                'label': short_period_label(period_type, key),
                'short_label': key if period_type == 'year' else (key[-2:] if period_type == 'month' else key.split('-W')[1]),
                'income': cents_to_amount(income),
                'expense': cents_to_amount(expense),
            }
        )
    return trend


def build_dashboard_payload(db_path: Path = DB_PATH) -> dict:
    transactions = fetch_transactions(db_path)
    grouped: dict[str, dict[str, list[dict]]] = {period_type: defaultdict(list) for period_type in PERIOD_TYPES}
    for row in transactions:
        grouped['year'][row['year']].append(row)
        grouped['month'][row['month']].append(row)
        grouped['week'][row['week']].append(row)

    controls = {'period_types': list(PERIOD_TYPES), 'options': {}, 'default_periods': {}}
    views = {}
    default_period_type = 'month'
    for period_type in PERIOD_TYPES:
        ordered_keys = sorted(grouped[period_type].keys())
        controls['default_periods'][period_type] = ordered_keys[-1]
        controls['options'][period_type] = [
            {'value': key, 'label': short_period_label(period_type, key)}
            for key in reversed(ordered_keys)
        ]
        views[period_type] = {
            'trend': build_trend_series(period_type, grouped[period_type], ordered_keys),
            'periods': {key: build_period_snapshot(period_type, key, grouped[period_type][key], grouped) for key in ordered_keys},
        }

    return {
        'summary': fetch_summary(db_path),
        'controls': controls,
        'default_period_type': default_period_type,
        'views': views,
    }


def list_categories(db_path: Path = DB_PATH) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute('SELECT DISTINCT category FROM transactions ORDER BY category').fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def list_subcategories(db_path: Path = DB_PATH) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute('SELECT name FROM subcategories ORDER BY name').fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def build_category_presets(db_path: Path = DB_PATH) -> list[dict]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    category_counts: Counter[str] = Counter()
    category_types: dict[str, Counter[str]] = defaultdict(Counter)
    for row in fetch_transactions(db_path):
        category = row['category']
        category_counts[category] += 1
        category_types[category][row['io_type']] += 1
        if row['subcategory']:
            grouped[category][row['subcategory']] += 1
    presets = []
    for category, count in category_counts.most_common():
        presets.append(
            {
                'name': category,
                'count': count,
                'io_types': [name for name, _ in category_types[category].most_common()],
                'subcategories': [
                    {'name': name, 'count': subcount}
                    for name, subcount in grouped[category].most_common(12)
                ],
            }
        )
    return presets


def build_month_detail(month: str, db_path: Path = DB_PATH) -> dict:
    transactions = [row for row in fetch_transactions(db_path) if row['month'] == month]
    income = sum(row['amount_cents'] for row in transactions if row['io_type'] == '收入')
    expense = sum(row['amount_cents'] for row in transactions if row['io_type'] == '支出')
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in sorted(transactions, key=lambda item: (item['occurred_on'], item['id']), reverse=True):
        by_day[row['occurred_on']].append(
            {
                'id': row['id'],
                'io_type': row['io_type'],
                'category': row['category'],
                'subcategory': row['subcategory'],
                'memo': row['memo'],
                'amount': row['amount'],
            }
        )
    days = []
    for day in sorted(by_day.keys(), reverse=True):
        items = by_day[day]
        day_expense = round(sum(item['amount'] for item in items if item['io_type'] == '支出'), 2)
        day_income = round(sum(item['amount'] for item in items if item['io_type'] == '收入'), 2)
        days.append(
            {
                'date': day,
                'weekday': weekday_label(day),
                'expense': day_expense,
                'income': day_income,
                'items': items,
            }
        )
    return {
        'month': month,
        'label': format_period_label('month', month),
        'income': cents_to_amount(income),
        'expense': cents_to_amount(expense),
        'days': days,
    }


def build_detail_bootstrap(db_path: Path = DB_PATH) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        months = [row[0] for row in conn.execute('SELECT DISTINCT month FROM transactions ORDER BY month DESC').fetchall()]
    finally:
        conn.close()
    years = sorted({month.split('-')[0] for month in months}, reverse=True)
    year_months = {year: [month for month in months if month.startswith(year + '-')] for year in years}
    default_month = months[0]
    default_year = default_month.split('-')[0]
    return {
        'years': years,
        'year_months': year_months,
        'default_year': default_year,
        'default_month': default_month,
        'categories': list_categories(db_path),
        'subcategories': list_subcategories(db_path),
        'category_presets': build_category_presets(db_path),
        'month_detail': build_month_detail(default_month, db_path),
    }
