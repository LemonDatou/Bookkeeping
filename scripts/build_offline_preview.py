from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / 'dashboard'
OUTPUT_DIR = ROOT / 'offline-preview'
sys.path.insert(0, str(ROOT / 'server'))

from payloads import DB_PATH, build_dashboard_payload, build_detail_bootstrap, build_month_detail


def inject_bootstrap(html: str, bootstrap_script: str) -> str:
    marker = '<script src="./app.js"></script>'
    if marker in html:
        return html.replace(marker, f'{bootstrap_script}\n    {marker}')
    marker = '<script src="./detail.js"></script>'
    if marker in html:
        return html.replace(marker, f'{bootstrap_script}\n    {marker}')
    raise ValueError('Unable to inject preview bootstrap script.')


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    for filename in ('styles.css', 'app.js', 'detail.css', 'detail.js'):
        shutil.copy2(DASHBOARD_DIR / filename, OUTPUT_DIR / filename)

    dashboard_payload = build_dashboard_payload(DB_PATH)
    detail_bootstrap = build_detail_bootstrap(DB_PATH)
    detail_months = {
        month: build_month_detail(month, DB_PATH)
        for months in detail_bootstrap['year_months'].values()
        for month in months
    }

    index_html = (DASHBOARD_DIR / 'index.html').read_text(encoding='utf-8')
    detail_html = (DASHBOARD_DIR / 'detail.html').read_text(encoding='utf-8')

    index_bootstrap = '<script>window.__BOOKKEEPING_PREVIEW__=' + json.dumps(dashboard_payload, ensure_ascii=False) + ';</script>'
    detail_bootstrap_script = (
        '<script>window.__BOOKKEEPING_DETAIL_BOOTSTRAP__='
        + json.dumps(detail_bootstrap, ensure_ascii=False)
        + ';window.__BOOKKEEPING_DETAIL_MONTHS__='
        + json.dumps(detail_months, ensure_ascii=False)
        + ';</script>'
    )

    (OUTPUT_DIR / 'index.html').write_text(inject_bootstrap(index_html, index_bootstrap), encoding='utf-8')
    (OUTPUT_DIR / 'detail.html').write_text(inject_bootstrap(detail_html, detail_bootstrap_script), encoding='utf-8')

    print(f'Offline preview generated at: {OUTPUT_DIR}')


if __name__ == '__main__':
    main()
