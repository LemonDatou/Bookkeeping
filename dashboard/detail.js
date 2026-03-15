let detailData = null;
let csrfToken = '';

const state = {
  year: '',
  month: '',
  renderedMonths: [],
  editingId: null,
};
const entryCache = new Map();

const els = {
  yearTabs: document.querySelector('#year-tabs'),
  monthTabs: document.querySelector('#month-tabs'),
  monthExpense: document.querySelector('#month-expense'),
  monthIncome: document.querySelector('#month-income'),
  monthFeed: document.querySelector('#month-feed'),
  addEntryBtn: document.querySelector('#add-entry-btn'),
  entryModal: document.querySelector('#entry-modal'),
  modalTitle: document.querySelector('#modal-title'),
  entryForm: document.querySelector('#entry-form'),
  deleteEntryBtn: document.querySelector('#delete-entry-btn'),
  categoryOptions: document.querySelector('#category-options'),
  subcategoryOptions: document.querySelector('#subcategory-options'),
};

const categoryIcons = {
  餐饮: '🍽️', 日用: '🧻', 交通: '🚌', 娱乐: '🎮', 住房: '🏠', 医疗: '💊', 数码: '💻',
  服饰: '👕', 工资: '💼', 其它: '📦', 宠物: '🐾', 社交: '🎁', 旅行: '🧳', 玩: '🎡',
};

function formatCurrency(value) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency', currency: 'CNY', minimumFractionDigits: value % 1 === 0 ? 0 : 2, maximumFractionDigits: 2,
  }).format(value);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
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
  if (response.status === 204) return null;
  return response.json();
}

function createButtons(container, values, activeValue, labeler, onClick) {
  container.innerHTML = values
    .map((value) => `<button class="segment-btn ${value === activeValue ? 'active' : ''}" data-value="${value}">${labeler(value)}</button>`)
    .join('');
  container.querySelectorAll('.segment-btn').forEach((button) => {
    button.addEventListener('click', () => onClick(button.dataset.value));
  });
}

function renderOptions() {
  els.categoryOptions.innerHTML = detailData.categories.map((item) => `<option value="${item}"></option>`).join('');
  els.subcategoryOptions.innerHTML = detailData.subcategories.map((item) => `<option value="${item}"></option>`).join('');
}

function monthLabel(month) {
  const [, mon] = month.split('-');
  return `${Number(mon)} 月`;
}

function renderYearTabs() {
  createButtons(els.yearTabs, detailData.years, state.year, (value) => `${value} 年`, async (value) => {
    state.year = value;
    state.month = detailData.year_months[value][0];
    await resetFeed();
  });
}

function renderMonthTabs() {
  createButtons(els.monthTabs, detailData.year_months[state.year], state.month, monthLabel, async (value) => {
    state.month = value;
    await resetFeed();
  });
}

function subtitle(item) {
  const parts = [];
  if (item.subcategory) parts.push(item.subcategory);
  if (item.memo) parts.push(item.memo);
  return parts.join(' · ');
}

function renderMonthSummary(monthDetail) {
  els.monthExpense.textContent = formatCurrency(monthDetail.expense);
  els.monthIncome.textContent = formatCurrency(monthDetail.income);
}

function monthCard(monthDetail) {
  return `
    <section class="month-section" data-month="${monthDetail.month}">
      <div class="month-header">
        <div>
          <h2>${monthDetail.label}</h2>
          <div class="month-meta">收入 ${formatCurrency(monthDetail.income)} · 支出 ${formatCurrency(monthDetail.expense)}</div>
        </div>
      </div>
      ${monthDetail.days
        .map(
          (day) => `
            <div class="day-block">
              <div class="day-header">
                <strong>${day.date.slice(5).replace('-', '月')}日 ${day.weekday}</strong>
                <span>${day.expense > 0 ? `支出：${formatCurrency(day.expense)}` : `收入：${formatCurrency(day.income)}`}</span>
              </div>
              ${day.items
                .map(
                  (item) => `
                    <article class="entry">
                      <div class="entry-main">
                        <div class="icon-badge">${categoryIcons[item.category] || '🧾'}</div>
                        <div>
                          <div class="entry-title">${item.category}</div>
                          <div class="entry-subtitle">${subtitle(item) || '未标记'}</div>
                        </div>
                      </div>
                      <div class="entry-actions">
                        <button type="button" class="icon-btn" data-edit-id="${item.id}">编辑</button>
                        <div class="entry-amount ${item.io_type === '收入' ? 'income' : 'expense'}">${item.io_type === '收入' ? '+' : '-'}${formatCurrency(item.amount)}</div>
                      </div>
                    </article>
                  `
                )
                .join('')}
            </div>
          `
        )
        .join('')}
    </section>
  `;
}

async function loadMonth(month) {
  return fetchJson(`/api/detail/months/${month}`);
}

function bindEditButtons() {
  document.querySelectorAll('[data-edit-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const id = Number(button.dataset.editId);
      const item = findEntryById(id);
      if (item) openModal(item);
    });
  });
}

function storeMonthDetail(monthDetail) {
  monthDetail.days.forEach((day) => {
    day.items.forEach((item) => {
      entryCache.set(item.id, { ...item, date: day.date });
    });
  });
}

async function resetFeed() {
  state.renderedMonths = [];
  entryCache.clear();
  els.monthFeed.innerHTML = '<div class="loading">加载中...</div>';
  renderYearTabs();
  renderMonthTabs();
  const monthDetail = await loadMonth(state.month);
  renderMonthSummary(monthDetail);
  storeMonthDetail(monthDetail);
  els.monthFeed.innerHTML = monthCard(monthDetail);
  state.renderedMonths.push(monthDetail.month);
  bindEditButtons();
}

async function appendMonth(month) {
  if (!month || state.renderedMonths.includes(month)) return;
  const monthDetail = await loadMonth(month);
  storeMonthDetail(monthDetail);
  els.monthFeed.insertAdjacentHTML('beforeend', monthCard(monthDetail));
  state.renderedMonths.push(monthDetail.month);
  bindEditButtons();
}

function nextMonth(month) {
  const months = detailData.year_months[state.year];
  const index = months.indexOf(month);
  if (index === -1 || index === months.length - 1) return null;
  return months[index + 1];
}

async function maybeAppendNextMonth() {
  const scrollBottom = window.innerHeight + window.scrollY;
  const threshold = document.body.offsetHeight - 120;
  if (scrollBottom < threshold) return;
  const currentLast = state.renderedMonths[state.renderedMonths.length - 1];
  const following = nextMonth(currentLast);
  if (following) await appendMonth(following);
}

function openModal(item = null) {
  state.editingId = item ? item.id : null;
  els.modalTitle.textContent = item ? '修改记账' : '新增记账';
  els.deleteEntryBtn.classList.toggle('hidden', !item);
  els.entryForm.reset();
  if (item) {
    els.entryForm.io_type.value = item.io_type;
    els.entryForm.occurred_on.value = item.date || state.month + '-01';
    els.entryForm.amount.value = item.amount;
    els.entryForm.category.value = item.category;
    els.entryForm.subcategory.value = item.subcategory || '';
    els.entryForm.memo.value = item.memo || '';
  } else {
    els.entryForm.io_type.value = '支出';
    els.entryForm.occurred_on.value = `${state.month}-01`;
  }
  els.entryModal.classList.remove('hidden');
}

function closeModal() {
  els.entryModal.classList.add('hidden');
  state.editingId = null;
}

function findEntryById(id) {
  return entryCache.get(id) || null;
}

async function refreshCurrentMonthCache(month) {
  const detail = await loadMonth(month);
  storeMonthDetail(detail);
  return detail;
}

async function reloadCurrentMonth() {
  const detail = await refreshCurrentMonthCache(state.month);
  renderMonthSummary(detail);
  const firstSection = els.monthFeed.querySelector(`.month-section[data-month="${state.month}"]`);
  if (firstSection) {
    firstSection.outerHTML = monthCard(detail);
  } else {
    els.monthFeed.innerHTML = monthCard(detail);
  }
  bindEditButtons();
}

function formPayload() {
  const form = new FormData(els.entryForm);
  return {
    io_type: form.get('io_type'),
    occurred_on: form.get('occurred_on'),
    amount: form.get('amount'),
    category: form.get('category'),
    subcategory: form.get('subcategory'),
    memo: form.get('memo'),
  };
}

async function submitEntry(event) {
  event.preventDefault();
  const payload = formPayload();
  try {
    if (state.editingId) {
      await fetchJson(`/api/transactions/${state.editingId}`, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      await fetchJson('/api/transactions', { method: 'POST', body: JSON.stringify(payload) });
    }
    detailData = await fetchJson('/api/detail/bootstrap');
    csrfToken = detailData.csrf_token;
    state.month = payload.occurred_on.slice(0, 7);
    state.year = state.month.slice(0, 4);
    renderOptions();
    await resetFeed();
    closeModal();
  } catch (error) {
    console.error(error);
    alert(error.message || '保存失败');
  }
}

async function deleteEntry() {
  if (!state.editingId) return;
  if (!window.confirm('确认删除这条记录吗？')) return;
  try {
    await fetchJson(`/api/transactions/${state.editingId}`, { method: 'DELETE' });
    detailData = await fetchJson('/api/detail/bootstrap');
    csrfToken = detailData.csrf_token;
    renderOptions();
    if (!detailData.year_months[state.year]?.includes(state.month)) {
      state.year = detailData.default_year;
      state.month = detailData.default_month;
    }
    await resetFeed();
    closeModal();
  } catch (error) {
    console.error(error);
    alert(error.message || '删除失败');
  }
}

async function init() {
  try {
    detailData = await fetchJson('/api/detail/bootstrap');
    csrfToken = detailData.csrf_token;
    state.year = detailData.default_year;
    state.month = detailData.default_month;
    renderOptions();
    await refreshCurrentMonthCache(state.month);
    await resetFeed();
    els.addEntryBtn.addEventListener('click', () => openModal());
    els.entryForm.addEventListener('submit', submitEntry);
    els.deleteEntryBtn.addEventListener('click', deleteEntry);
    els.entryModal.addEventListener('click', (event) => {
      if (event.target.dataset.close) closeModal();
    });
    window.addEventListener('scroll', () => {
      maybeAppendNextMonth().catch(console.error);
    }, { passive: true });
  } catch (error) {
    console.error(error);
    alert('加载明细失败');
  }
}

init();
