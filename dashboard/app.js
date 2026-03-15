let dashboardData = null;

const previewData = window.__BOOKKEEPING_PREVIEW__ || null;

const state = {
  periodType: 'month',
  periodKey: '',
  rankingMode: 'secondary',
  overviewExpanded: false,
};

const els = {
  periodTypeTabs: document.querySelector('#period-type-tabs'),
  periodSelect: document.querySelector('#period-select'),
  periodBadge: document.querySelector('#period-badge'),
  expenseTotal: document.querySelector('#expense-total'),
  incomeTotal: document.querySelector('#income-total'),
  balanceTotal: document.querySelector('#balance-total'),
  trendCaption: document.querySelector('#trend-caption'),
  trendChart: document.querySelector('#trend-chart'),
  rankingTabs: document.querySelector('#ranking-tabs'),
  categoryList: document.querySelector('#category-list'),
  topDays: document.querySelector('#top-days'),
  overviewTitle: document.querySelector('#overview-title'),
  overviewCaption: document.querySelector('#overview-caption'),
  overviewToggle: document.querySelector('#overview-toggle'),
  overviewPanel: document.querySelector('#overview-panel'),
  yearlyList: document.querySelector('#yearly-list'),
};

function formatCurrency(value) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(value);
}

async function fetchJson(url, options = {}) {
  if (previewData && url === '/api/dashboard') {
    return previewData;
  }
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (response.status === 401) {
    window.location.href = '/login';
    throw new Error('unauthorized');
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `Request failed: ${response.status}`);
  }
  return response.json();
}

function periodTypeLabel(periodType) {
  return { year: '年', month: '月', week: '周' }[periodType] || periodType;
}

function createSegmentedButtons(container, items, activeValue, onClick) {
  container.innerHTML = items
    .map((item) => `<button class="segment-btn ${item.value === activeValue ? 'active' : ''}" data-value="${item.value}">${item.label}</button>`)
    .join('');
  container.querySelectorAll('.segment-btn').forEach((button) => {
    button.addEventListener('click', () => onClick(button.dataset.value));
  });
}

function getCurrentView() {
  return dashboardData.views[state.periodType];
}

function getCurrentSnapshot() {
  return getCurrentView().periods[state.periodKey];
}

function syncOverviewDisclosure() {
  const expanded = state.overviewExpanded;
  els.overviewPanel.classList.toggle('hidden', !expanded);
  els.overviewToggle.setAttribute('aria-expanded', String(expanded));
  els.overviewToggle.querySelector('span').textContent = expanded ? '收起总览' : '展开总览';
}

function renderPeriodTypeTabs() {
  createSegmentedButtons(
    els.periodTypeTabs,
    dashboardData.controls.period_types.map((value) => ({ value, label: periodTypeLabel(value) })),
    state.periodType,
    (value) => {
      state.periodType = value;
      state.periodKey = dashboardData.controls.default_periods[value];
      render();
    }
  );
}

function renderPeriodSelect() {
  const options = dashboardData.controls.options[state.periodType];
  els.periodSelect.innerHTML = options
    .map(
      (item) => `
        <button class="period-chip ${item.value === state.periodKey ? 'active' : ''}" data-value="${item.value}">
          ${item.label}
        </button>
      `
    )
    .join('');
  const activeButton = els.periodSelect.querySelector('.period-chip.active');
  els.periodSelect.querySelectorAll('.period-chip').forEach((button) => {
    button.addEventListener('click', () => {
      state.periodKey = button.dataset.value;
      render();
    });
  });
  if (activeButton) {
    requestAnimationFrame(() => {
      activeButton.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    });
  }
}

function renderTrend() {
  const series = getCurrentView().trend;
  const width = 960;
  const height = window.innerWidth <= 720 ? 156 : 188;
  const baseline = height - 26;
  const top = 18;
  const left = 20;
  const right = 20;
  const max = Math.max(...series.flatMap((item) => [item.expense, item.income]), 1);
  const stepX = series.length > 1 ? (width - left - right) / (series.length - 1) : 0;

  els.trendCaption.textContent = `最近 ${series.length} 个${periodTypeLabel(state.periodType)}周期`;

  const pointsFor = (key) =>
    series
      .map((item, index) => {
        const x = left + index * stepX;
        const y = baseline - (item[key] / max) * (baseline - top);
        return `${x},${y}`;
      })
      .join(' ');

  const dotsFor = (key, color) =>
    series
      .map((item, index) => {
        const x = left + index * stepX;
        const y = baseline - (item[key] / max) * (baseline - top);
        return `<circle cx="${x}" cy="${y}" r="3.5" fill="${color}"></circle>`;
      })
      .join('');

  const labels = series
    .map((item, index) => {
      const x = left + index * stepX;
      return `<text x="${x}" y="${height - 6}" text-anchor="middle" fill="#8f96a3" font-size="10">${item.short_label}</text>`;
    })
    .join('');

  els.trendChart.innerHTML = `
    <svg class="trend-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="趋势图">
      <line x1="${left}" y1="${baseline}" x2="${width - right}" y2="${baseline}" stroke="rgba(31,41,55,0.08)" />
      <polyline fill="none" stroke="#ff7a59" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="${pointsFor('expense')}" />
      <polyline fill="none" stroke="#22a06b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="${pointsFor('income')}" />
      ${dotsFor('expense', '#ff7a59')}
      ${dotsFor('income', '#22a06b')}
      ${labels}
    </svg>
    <div class="trend-legend">
      <span><i class="legend-dot" style="background:#ff7a59"></i>支出</span>
      <span><i class="legend-dot" style="background:#22a06b"></i>收入</span>
    </div>
  `;
}

function renderRankingTabs() {
  createSegmentedButtons(
    els.rankingTabs,
    [
      { value: 'secondary', label: '二级分类' },
      { value: 'primary', label: '一级分类' },
    ],
    state.rankingMode,
    (value) => {
      state.rankingMode = value;
      render();
    }
  );
}

function renderRanking(snapshot) {
  const list = snapshot.rankings[state.rankingMode];
  els.categoryList.innerHTML = list.length
    ? list
        .map(
          (item) => `
            <article class="category-item">
              <div class="category-row">
                <strong>${item.name}</strong>
                <span class="amount expense">${formatCurrency(item.amount)}</span>
              </div>
              <div class="progress"><span style="width:${Math.max(item.share * 100, 6)}%"></span></div>
              <div class="secondary">占当前周期支出的 ${(item.share * 100).toFixed(1)}%</div>
            </article>
          `
        )
        .join('')
    : '<article class="category-item"><div class="secondary">当前周期没有已标记二级分类消费。</div></article>';
}

function renderSingleExpenseTop(snapshot) {
  els.topDays.innerHTML = snapshot.single_expense_top10
    .map(
      (item) => `
        <article class="day-item">
          <div class="day-row">
            <div>
              <strong>${item.category}</strong>
              <div class="secondary">${item.subcategory || '未标记'}${item.memo ? ` · ${item.memo}` : ''} · ${item.date}</div>
            </div>
            <span class="amount expense">${formatCurrency(item.amount)}</span>
          </div>
        </article>
      `
    )
    .join('');
}

function renderOverview(snapshot) {
  const titleMap = { year: '年度总览', month: '月度总览', week: '周度总览' };
  const captionMap = { year: '所有年份', month: '当前年份内各月份', week: '当前月份内各周' };
  els.overviewTitle.textContent = titleMap[state.periodType];
  els.overviewCaption.textContent = captionMap[state.periodType];
  els.yearlyList.innerHTML = snapshot.overview_rows
    .map(
      (item) => `
        <article class="year-item">
          <div class="year-row">
            <strong>${item.label}</strong>
            <div class="year-metrics">
              <span class="amount income">收入 ${formatCurrency(item.income)}</span>
              <span class="amount expense">支出 ${formatCurrency(item.expense)}</span>
              <span class="amount balance">结余 ${formatCurrency(item.balance)}</span>
            </div>
          </div>
        </article>
      `
    )
    .join('');
}

function renderSnapshotSections() {
  const snapshot = getCurrentSnapshot();
  els.periodBadge.textContent = snapshot.label;
  els.expenseTotal.textContent = formatCurrency(snapshot.expense);
  els.incomeTotal.textContent = formatCurrency(snapshot.income);
  els.balanceTotal.textContent = formatCurrency(snapshot.balance);
  renderRanking(snapshot);
  renderSingleExpenseTop(snapshot);
  renderOverview(snapshot);
  syncOverviewDisclosure();
}

function render() {
  renderPeriodTypeTabs();
  renderPeriodSelect();
  renderTrend();
  renderRankingTabs();
  renderSnapshotSections();
}

async function init() {
  try {
    dashboardData = await fetchJson('/api/dashboard');
    state.periodType = dashboardData.default_period_type;
    state.periodKey = dashboardData.controls.default_periods[state.periodType];
    els.overviewToggle.addEventListener('click', () => {
      state.overviewExpanded = !state.overviewExpanded;
      syncOverviewDisclosure();
    });
    render();
  } catch (error) {
    console.error(error);
    alert('加载看板失败');
  }
}

init();
