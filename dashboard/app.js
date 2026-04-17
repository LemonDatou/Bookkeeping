let dashboardData = null;

const previewData = window.__BOOKKEEPING_PREVIEW__ || null;

const state = {
  periodType: 'month',
  periodKey: '',
  rankingMode: 'secondary',
  overviewExpanded: false,
  loading: false,
};

const snapshotCache = {};

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
  overviewToggleRow: document.querySelector('#overview-toggle-row'),
  overviewToggle: document.querySelector('#overview-toggle'),
  overviewPanel: document.querySelector('#overview-panel'),
  overviewSummary: document.querySelector('#overview-summary'),
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

function truncateMiddle(text, maxLength = 18) {
  const value = String(text || '');
  if (value.length <= maxLength) return value;
  const head = Math.ceil((maxLength - 1) / 2);
  const tail = Math.floor((maxLength - 1) / 2);
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

async function fetchJson(url, options = {}) {
  if (previewData && url === '/api/dashboard/init') {
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

async function getSnapshot(periodType, periodKey) {
  const cacheKey = `${periodType}:${periodKey}`;
  if (snapshotCache[cacheKey]) return snapshotCache[cacheKey];
  const data = await fetchJson(`/api/dashboard/snapshot?type=${periodType}&key=${periodKey}`);
  snapshotCache[cacheKey] = data;
  return data;
}

function getCurrentTrend() {
  return dashboardData.trend[state.periodType];
}

function isOverviewAlwaysExpanded() {
  return state.periodType === 'year';
}

function syncOverviewDisclosure() {
  const expanded = isOverviewAlwaysExpanded() || state.overviewExpanded;
  els.overviewToggleRow.classList.toggle('hidden', isOverviewAlwaysExpanded());
  els.overviewPanel.classList.toggle('hidden', !expanded);
  els.overviewToggle.setAttribute('aria-expanded', String(expanded));
  els.overviewToggle.querySelector('span').textContent = expanded ? '收起总览' : '展开总览';
}

function renderPeriodTypeTabs() {
  createSegmentedButtons(
    els.periodTypeTabs,
    dashboardData.controls.period_types.map((value) => ({ value, label: periodTypeLabel(value) })),
    state.periodType,
    async (value) => {
      state.periodType = value;
      state.periodKey = dashboardData.controls.default_periods[value];
      await renderAsync();
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
    button.addEventListener('click', async () => {
      state.periodKey = button.dataset.value;
      await renderAsync();
    });
  });
  if (activeButton) {
    requestAnimationFrame(() => {
      activeButton.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    });
  }
}

function renderTrend() {
  const series = getCurrentTrend();
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
    async (value) => {
      state.rankingMode = value;
      await renderAsync();
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
              <strong>${item.category} · ${item.date}</strong>
              <div class="secondary">${item.subcategory || '未标记'}${item.memo ? ` · ${truncateMiddle(item.memo)}` : ''}</div>
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
  const totals = snapshot.overview_rows.reduce(
    (acc, item) => ({
      expense: acc.expense + item.expense,
      income: acc.income + item.income,
      balance: acc.balance + item.balance,
    }),
    { expense: 0, income: 0, balance: 0 }
  );
  els.overviewSummary.innerHTML = `
    <article class="overview-summary-card">
      <div class="overview-grid">
        <strong class="overview-grid-head">${titleMap[state.periodType]}</strong>
        <div class="year-metrics">
          <span class="amount expense">支出 ${formatCurrency(totals.expense)}</span>
          <span class="amount income">收入 ${formatCurrency(totals.income)}</span>
          <span class="amount balance">结余 ${formatCurrency(totals.balance)}</span>
        </div>
      </div>
    </article>
  `;
  els.yearlyList.innerHTML = snapshot.overview_rows
    .map(
      (item) => `
        <article class="year-item">
          <div class="year-row">
            <strong>${item.label}</strong>
            <div class="year-metrics">
              <span class="amount expense">支出 ${formatCurrency(item.expense)}</span>
              <span class="amount income">收入 ${formatCurrency(item.income)}</span>
              <span class="amount balance">结余 ${formatCurrency(item.balance)}</span>
            </div>
          </div>
        </article>
      `
    )
    .join('');
}

function renderSnapshotSections(snapshot) {
  els.periodBadge.textContent = snapshot.label;
  els.expenseTotal.textContent = formatCurrency(snapshot.expense);
  els.incomeTotal.textContent = formatCurrency(snapshot.income);
  els.balanceTotal.textContent = formatCurrency(snapshot.balance);
  renderRanking(snapshot);
  renderSingleExpenseTop(snapshot);
  renderOverview(snapshot);
  syncOverviewDisclosure();
}

async function renderAsync() {
  if (state.loading) return;
  state.loading = true;
  try {
    renderPeriodTypeTabs();
    renderPeriodSelect();
    renderRankingTabs();
    renderTrend();
    const snapshot = await getSnapshot(state.periodType, state.periodKey);
    renderSnapshotSections(snapshot);
  } finally {
    state.loading = false;
  }
}

async function init() {
  try {
    dashboardData = await fetchJson('/api/dashboard/init');

    state.periodType = dashboardData.default_period_type;
    state.periodKey = dashboardData.controls.default_periods[state.periodType];

    // 将初始快照放入缓存
    if (dashboardData.default_snapshot && dashboardData.default_snapshot.key) {
      const cacheKey = `${state.periodType}:${state.periodKey}`;
      snapshotCache[cacheKey] = dashboardData.default_snapshot;
    }

    els.overviewToggle.addEventListener('click', () => {
      state.overviewExpanded = !state.overviewExpanded;
      syncOverviewDisclosure();
    });

    await renderAsync();
  } catch (error) {
    console.error(error);
    alert('加载看板失败');
  }
}

init();
