let detailData = null;
let csrfToken = '';

const previewBootstrap = window.__BOOKKEEPING_DETAIL_BOOTSTRAP__ || null;
const previewMonths = window.__BOOKKEEPING_DETAIL_MONTHS__ || null;
const isOfflinePreview = Boolean(previewBootstrap);

const state = {
  year: '',
  month: '',
  renderedMonths: [],
  editingId: null,
  selectedCategory: '',
  categoryPickerExpanded: true,
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
  categoryToggle: document.querySelector('#category-toggle'),
  selectedCategory: document.querySelector('#selected-category'),
  categoryPicker: document.querySelector('#category-picker'),
  subcategoryPicker: document.querySelector('#subcategory-picker'),
  subcategoryOptions: document.querySelector('#subcategory-options'),
};

const categoryIcons = {
  餐饮: '🍽️', 日用: '🧻', 交通: '🚌', 娱乐: '🎮', 住房: '🏠', 医疗: '💊', 数码: '💻',
  服饰: '👕', 工资: '💼', 其它: '📦', 宠物: '🐾', 社交: '🎁', 旅行: '🧳', 玩: '🎡',
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
  if (previewBootstrap) {
    if (url === '/api/detail/bootstrap') return previewBootstrap;
    if (url.startsWith('/api/detail/months/')) {
      const month = url.slice('/api/detail/months/'.length);
      return previewMonths?.[month] || previewBootstrap.month_detail;
    }
    throw new Error('离线预览不支持写入操作');
  }
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

function getCategoryPresets() {
  return detailData.category_presets || detailData.categories.map((name) => ({ name, subcategories: [], io_types: [] }));
}

function currentIoType() {
  return els.entryForm.io_type.value || '支出';
}

function sortedCategoriesForCurrentType() {
  const ioType = currentIoType();
  return getCategoryPresets()
    .slice()
    .sort((left, right) => {
      const leftHit = left.io_types?.includes(ioType) ? 1 : 0;
      const rightHit = right.io_types?.includes(ioType) ? 1 : 0;
      if (leftHit !== rightHit) return rightHit - leftHit;
      return (right.count || 0) - (left.count || 0);
    });
}

function renderOptions() {
  els.subcategoryOptions.innerHTML = detailData.subcategories.map((item) => `<option value="${item}"></option>`).join('');
}

function todayString() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function syncCategoryPickerVisibility() {
  const category = els.entryForm.category.value || state.selectedCategory;
  els.categoryPicker.classList.toggle('hidden', !state.categoryPickerExpanded);
  els.selectedCategory.classList.toggle('hidden', !category || state.categoryPickerExpanded);
  if (category && !state.categoryPickerExpanded) {
    els.selectedCategory.innerHTML = `<span class="category-icon">${categoryIcons[category] || '🧾'}</span><strong>${category}</strong>`;
  } else {
    els.selectedCategory.innerHTML = '';
  }
  els.categoryToggle.textContent = state.categoryPickerExpanded ? '收起' : category ? '修改' : '展开';
}

function renderCategoryPicker() {
  const selected = els.entryForm.category.value || state.selectedCategory;
  els.categoryPicker.innerHTML = sortedCategoriesForCurrentType()
    .map((item) => {
      const active = item.name === selected;
      const icon = categoryIcons[item.name] || '🧾';
      return `
        <button type="button" class="category-option ${active ? 'active' : ''}" data-category="${item.name}">
          <span class="category-icon">${icon}</span>
          <span class="category-name">${item.name}</span>
        </button>
      `;
    })
    .join('');
  els.categoryPicker.querySelectorAll('[data-category]').forEach((button) => {
    button.addEventListener('click', () => {
      state.selectedCategory = button.dataset.category;
      els.entryForm.category.value = state.selectedCategory;
      els.entryForm.subcategory.value = '';
      state.categoryPickerExpanded = false;
      renderCategoryPicker();
      renderSubcategoryPicker();
      syncCategoryPickerVisibility();
    });
  });
  syncCategoryPickerVisibility();
}

function renderSubcategoryPicker() {
  const category = els.entryForm.category.value || state.selectedCategory;
  const preset = getCategoryPresets().find((item) => item.name === category);
  const selected = els.entryForm.subcategory.value || '';
  const options = preset?.subcategories || [];
  els.subcategoryPicker.innerHTML = options.length
    ? options
        .map((item) => `
          <button type="button" class="subcategory-chip ${item.name === selected ? 'active' : ''}" data-subcategory="${item.name}">
            ${item.name}
          </button>
        `)
        .join('')
    : '<div class="picker-hint">先选一级分类，再从这里点选常用二级分类。</div>';
  els.subcategoryPicker.querySelectorAll('[data-subcategory]').forEach((button) => {
    button.addEventListener('click', () => {
      els.entryForm.subcategory.value = button.dataset.subcategory;
      renderSubcategoryPicker();
    });
  });
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
  if (item.memo) parts.push(truncateMiddle(item.memo));
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
                        <button type="button" class="icon-badge icon-edit-trigger" data-edit-id="${item.id}" aria-label="编辑 ${item.category}">${categoryIcons[item.category] || '🧾'}</button>
                        <div>
                          <div class="entry-title">${item.category}</div>
                          <div class="entry-subtitle">${subtitle(item) || '未标记'}</div>
                        </div>
                      </div>
                      <div class="entry-actions">
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
  els.deleteEntryBtn.classList.toggle('hidden', !item || isOfflinePreview);
  els.entryForm.reset();
  const defaultDate = todayString();
  if (item) {
    els.entryForm.io_type.value = item.io_type;
    els.entryForm.occurred_on.value = item.date || defaultDate;
    els.entryForm.amount.value = item.amount;
    els.entryForm.category.value = item.category;
    els.entryForm.subcategory.value = item.subcategory || '';
    els.entryForm.memo.value = item.memo || '';
    state.selectedCategory = item.category;
    state.categoryPickerExpanded = false;
  } else {
    els.entryForm.io_type.value = '支出';
    els.entryForm.occurred_on.value = defaultDate;
    els.entryForm.category.value = '';
    els.entryForm.subcategory.value = '';
    state.selectedCategory = '';
    state.categoryPickerExpanded = true;
  }
  renderCategoryPicker();
  renderSubcategoryPicker();
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
  if (isOfflinePreview) {
    alert('离线预览页仅供查看，不能直接保存。');
    return;
  }
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
  if (!state.editingId || isOfflinePreview) return;
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
    csrfToken = detailData.csrf_token || '';
    state.year = detailData.default_year;
    state.month = detailData.default_month;
    renderOptions();
    await refreshCurrentMonthCache(state.month);
    await resetFeed();
    els.addEntryBtn.addEventListener('click', () => openModal());
    els.entryForm.addEventListener('submit', submitEntry);
    els.categoryToggle.addEventListener('click', () => {
      state.categoryPickerExpanded = !state.categoryPickerExpanded;
      syncCategoryPickerVisibility();
    });
    els.entryForm.io_type.addEventListener('change', () => {
      const candidates = sortedCategoriesForCurrentType();
      if (!candidates.some((item) => item.name === els.entryForm.category.value)) {
        state.selectedCategory = '';
        els.entryForm.category.value = '';
        els.entryForm.subcategory.value = '';
        state.categoryPickerExpanded = true;
      }
      renderCategoryPicker();
      renderSubcategoryPicker();
    });
    els.entryForm.subcategory.addEventListener('input', () => {
      renderSubcategoryPicker();
    });
    els.deleteEntryBtn.addEventListener('click', deleteEntry);
    els.entryModal.addEventListener('click', (event) => {
      if (event.target.dataset.close) closeModal();
    });
    window.addEventListener('scroll', () => {
      maybeAppendNextMonth().catch(console.error);
    }, { passive: true });
    if (isOfflinePreview) {
      els.addEntryBtn.textContent = '离线预览';
    }
  } catch (error) {
    console.error(error);
    alert('加载明细失败');
  }
}

init();
