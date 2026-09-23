(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const statusNames = { todo: 'Запланировано', in_progress: 'В работе', blocked: 'Нужна помощь', done: 'Готово' };
  const departmentCodes = { director: 'DR', marketing: 'MK', sales: 'SL', development: 'DV', support: 'SP' };
  const views = {
    overview: ['Обзор', 'Операционный обзор', 'Ассистент, продажи и работа команды — в одном месте.'],
    tasks: ['Задачи', 'Работа команды', 'Приоритеты, ответственные и сроки по всем направлениям.'],
    team: ['Департаменты', 'Структура компании', 'Зоны ответственности и нагрузка команды EKT.'],
    content: ['Контент сайта', 'Слова, которые видит клиент', 'Актуальное приветствие и условия покупки в одном месте.'],
    catalog: ['Каталог и остатки', 'Актуальность каталога', 'Следите за обновлением товаров, цен и остатков EKT.'],
  };
  const state = { departments: [], tasks: [], summary: null, content: null, view: 'overview', taskView: 'board', loadId: 0, token: '', contentDirty: false, contentLoaded: false, toastTimer: null, sync: null, syncTimer: null, syncLoading: false };
  try { state.token = sessionStorage.getItem('ekt_admin_token') || ''; } catch (_) { /* Browser may block session storage. */ }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function clear(node) { node.replaceChildren(); return node; }
  function formatNumber(value) {
    if (value === null || value === undefined || value === '') return 'Нет данных';
    if (typeof value === 'number') return Number.isFinite(value) ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: value < 1 && value > 0 ? 4 : 2 }).format(value) : 'Нет данных';
    return String(value);
  }
  function localDate(date) {
    const offset = date.getTimezoneOffset();
    return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
  }
  function displayDate(value, includeYear = false) {
    if (!value) return 'Без срока';
    const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? `${value}T12:00:00` : value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', ...(includeYear ? { year: 'numeric' } : {}) });
  }
  function displayTime(value) {
    if (!value) return '';
    const parsed = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  function isOverdue(task) { return task.status !== 'done' && task.due_date && task.due_date < localDate(new Date()); }
  function departmentName(id) { return state.departments.find(dept => dept.id === id)?.name || id || 'Без департамента'; }
  function safeStatus(value) { return Object.hasOwn(statusNames, value) ? value : 'todo'; }
  function toast(message, error = false) {
    const node = $('#toast');
    clearTimeout(state.toastTimer);
    node.textContent = message;
    node.className = `toast${error ? ' error' : ''}`;
    node.hidden = false;
    state.toastTimer = setTimeout(() => { node.hidden = true; }, error ? 6500 : 3600);
  }
  function authHeaders() { return state.token ? { 'X-Admin-Token': state.token } : {}; }
  function getFilters() {
    const query = new URLSearchParams();
    query.set('department', $('#department').value || 'all');
    const period = $('#period').value;
    let start = '', end = '';
    if (period === 'custom') {
      start = $('#date-from').value;
      end = $('#date-to').value;
      if (start && end && start > end) throw new Error('Дата начала должна быть раньше даты окончания.');
    } else if (period !== 'all') {
      const today = new Date();
      end = localDate(today);
      today.setDate(today.getDate() - (period === 'today' ? 0 : Number(period) - 1));
      start = localDate(today);
    }
    if (start) query.set('from', start);
    if (end) query.set('to', end);
    return query.toString();
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(`/api/admin${path}`, { ...options, cache: 'no-store', signal: controller.signal, headers: { ...authHeaders(), ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
      if (!response.ok) {
        let detail;
        try {
          const body = await response.json();
          detail = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('; ') : body.error;
        } catch (_) { /* HTTP status remains the useful fallback. */ }
        const error = new Error(response.status === 401 || response.status === 403 ? 'Нужен действующий ключ администратора. Откройте настройки доступа.' : detail || `Не удалось получить данные (HTTP ${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return options.binary ? response.blob() : response.status === 204 ? null : response.json();
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Сервер отвечает дольше обычного. Обновите данные через несколько секунд.');
      if (error instanceof TypeError) throw new Error('Нет соединения с сервером. Проверьте подключение и повторите запрос.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function setConnection(ready, label) {
    $('#connection-dot').className = `connection-dot${ready === true ? ' ready' : ready === false ? ' error' : ''}`;
    $('#connection-text').textContent = label;
  }
  function showPageError(message) { $('#page-error').textContent = message || ''; $('#page-error').hidden = !message; }
  function empty(target, title, description, action) {
    const block = el('div', 'empty-state compact');
    if (title) block.append(el('strong', '', title));
    if (description) block.append(el('div', '', description));
    if (action) {
      const button = el('button', 'button button-light', action.label);
      button.type = 'button'; button.addEventListener('click', action.callback); block.append(button);
    }
    clear(target).append(block);
  }

  async function loadAll() {
    let query;
    try { query = getFilters(); } catch (error) { showPageError(error.message); return; }
    const id = ++state.loadId;
    $('#loading-line').hidden = false;
    $('#refresh').classList.add('busy-spin');
    $('#refresh').disabled = true;
    showPageError('');
    const results = await Promise.allSettled([
      api(`/summary?${query}`), api(`/departments?${query}`), api(`/tasks?${query}`), api('/content'),
    ]);
    if (id !== state.loadId) return;
    const errors = [];
    if (results[0].status === 'fulfilled') {
      state.summary = results[0].value;
      renderSummary();
    } else {
      state.summary = null;
      errors.push(results[0].reason);
      empty($('#metric-strip'), 'Показатели недоступны', 'Обновите страницу или проверьте доступ.');
      empty($('#monitor-content'), 'Не удалось загрузить измерения', 'Значения появятся после восстановления соединения.');
      clear($('#task-summary')); clear($('#data-notes'));
    }
    if (results[1].status === 'fulfilled') {
      state.departments = Array.isArray(results[1].value.departments) ? results[1].value.departments : [];
      renderDepartments();
    } else {
      errors.push(results[1].reason);
      empty($('#department-list'), 'Структура недоступна', 'Не удалось получить список департаментов.');
      empty($('#org-tree'), 'Структура недоступна', 'Не удалось получить список департаментов.');
    }
    if (results[2].status === 'fulfilled') {
      state.tasks = Array.isArray(results[2].value.tasks) ? results[2].value.tasks : [];
      renderTasks(); renderFocus();
      $('#nav-task-count').textContent = state.tasks.filter(task => task.status !== 'done').length;
    } else {
      errors.push(results[2].reason);
      state.tasks = [];
      $('#nav-task-count').textContent = '—';
      empty($('#task-board'), 'Задачи недоступны', 'Обновите данные, чтобы продолжить.');
      empty($('#task-table'), 'Задачи недоступны', 'Обновите данные, чтобы продолжить.');
      empty($('#focus-tasks'), 'Не удалось загрузить задачи');
    }
    if (results[3].status === 'fulfilled') {
      state.content = results[3].value;
      state.contentLoaded = true;
      if (!state.contentDirty) renderContent();
    } else {
      errors.push(results[3].reason);
      state.contentLoaded = false;
      $('#content-status').textContent = 'Не удалось загрузить тексты. Обновите данные.';
    }
    $('#content-save').disabled = !state.contentLoaded;
    $('#new-task').disabled = state.departments.length === 0;
    if (errors.length) {
      const unique = [...new Set(errors.map(error => error.message))];
      showPageError(unique.join(' '));
      setConnection(false, 'Есть ошибки загрузки');
      $('#updated-at').textContent = 'Данные загружены частично';
      if (errors.some(error => error.status === 401 || error.status === 403)) openAccess();
    } else {
      setConnection(true, 'Данные подключены');
      const time = displayTime(state.summary?.generated_at) || displayTime(new Date());
      $('#updated-at').textContent = `Обновлено в ${time}`;
    }
    $('#loading-line').hidden = true;
    $('#refresh').classList.remove('busy-spin');
    $('#refresh').disabled = false;
  }

  function metricContent(metric, className) {
    const missing = metric.value === null || metric.value === undefined;
    const value = el('div', `${className}${missing ? ' no-data' : ''}`, formatNumber(metric.value));
    if (!missing && metric.unit) value.append(el('small', '', metric.unit));
    return value;
  }
  function renderSummary() {
    const metrics = Array.isArray(state.summary?.metrics) ? state.summary.metrics : [];
    const featured = [];
    ['sessions', 'latency_avg', 'confirmed_proposals', 'cost_usd'].forEach(key => {
      const found = metrics.find(metric => metric.key === key);
      if (found) featured.push(found);
    });
    for (const metric of metrics) { if (featured.length < 4 && !featured.includes(metric)) featured.push(metric); }
    const strip = clear($('#metric-strip'));
    strip.style.setProperty('--metrics-count', Math.max(1, featured.length));
    if (!featured.length) empty(strip, 'Пока нет измерений', 'Показатели появятся после первых обращений к ассистенту.');
    else featured.forEach(metric => {
      const article = el('article', 'metric');
      const label = el('div', 'metric-label'); label.append(el('span', 'metric-dot'), el('span', '', metric.label || metric.key));
      article.append(label, metricContent(metric, 'metric-value'), el('div', 'metric-caption', metric.note || (metric.source ? `Источник: ${sourceLabel(metric.source)}` : 'За выбранный период')));
      strip.append(article);
    });
    const monitor = clear($('#monitor-content'));
    const monitoring = metrics.filter(metric => !featured.includes(metric));
    const monitoringOrder = ['latency_p95', 'input_tokens', 'output_tokens', 'errors', 'tool_calls', 'manager_activations'];
    monitoring.sort((a, b) => {
      const index = key => monitoringOrder.includes(key) ? monitoringOrder.indexOf(key) : 99;
      return index(a.key) - index(b.key);
    });
    let additional;
    if (!monitoring.length && !metrics.length) empty(monitor, 'Данных пока нет', 'Они появятся после работы ассистента.');
    else (monitoring.length ? monitoring : metrics).forEach((metric, index) => {
      const row = el('div', 'monitor-row');
      const label = el('div', 'monitor-label', metric.label || metric.key);
      if (metric.note || metric.source) label.append(el('span', 'monitor-source', metric.note || sourceLabel(metric.source)));
      row.append(label, metricContent(metric, 'monitor-value'));
      if (index < 6) monitor.append(row);
      else {
        if (!additional) {
          additional = el('details', 'additional-metrics');
          additional.append(el('summary', '', 'Остальные показатели'));
          monitor.append(additional);
        }
        additional.append(row);
      }
    });
    const counts = state.summary.task_counts || {};
    clear($('#task-summary'));
    Object.entries(statusNames).forEach(([status, name]) => {
      const item = el('div', `task-summary-item ${status}`);
      item.append(el('strong', '', counts[status] ?? '—'), el('span', '', name), el('span', 'summary-bar'));
      $('#task-summary').append(item);
    });
    const notes = clear($('#data-notes'));
    notes.append(el('p', '', 'Периоды и ежедневные показатели рассчитаны по времени UTC.'));
    const limitations = Array.isArray(state.summary.limitations) ? state.summary.limitations : [];
    if (limitations.length) {
      const details = el('details'); details.append(el('summary', '', 'О данных и расчётах'));
      limitations.forEach(note => details.append(el('p', '', note)));
      notes.append(details);
    }
    if (Array.isArray(state.summary.recent_events) && state.summary.recent_events.length) {
      const events = el('div', 'event-list');
      state.summary.recent_events.slice(0, 5).forEach(event => {
        const row = el('div', `event-row${event.error ? ' error' : ''}`);
        const label = el('div'); label.append(el('strong', '', event.event || event.type || 'Обращение к ассистенту'));
        if (event.model) label.append(el('small', '', event.model));
        row.append(label, el('span', '', [displayTime(event.timestamp), event.latency_ms !== undefined ? `${formatNumber(event.latency_ms)} мс` : ''].filter(Boolean).join(' · ')));
        events.append(row);
      });
      monitor.append(events);
    }
  }
  function sourceLabel(source) {
    const names = { assistant_db: 'База ассистента', 'assistant.sqlite': 'База ассистента', metrics_log: 'Журнал измерений', 'metrics.jsonl': 'Журнал измерений', database: 'База ассистента', tasks: 'Задачи команды', admin_db: 'Задачи команды', 'cms.sqlite': 'События команды' };
    return names[source] || source || 'Сохранённые данные';
  }

  function renderDepartments() {
    const selected = $('#department').value;
    const taskSelected = $('#task-department').value;
    clear($('#department')).append(new Option('Все департаменты', 'all'));
    clear($('#task-department'));
    state.departments.forEach(dept => {
      $('#department').append(new Option(dept.name, dept.id));
      $('#task-department').append(new Option(dept.name, dept.id));
    });
    if (state.departments.some(dept => dept.id === selected)) $('#department').value = selected;
    if (state.departments.some(dept => dept.id === taskSelected)) $('#task-department').value = taskSelected;
    const list = clear($('#department-list'));
    const departments = state.departments.filter(dept => dept.parent_id || dept.id !== 'director');
    if (!departments.length) empty(list, 'Департаменты не настроены');
    departments.forEach(dept => {
      const node = el('button', 'department-item');
      node.type = 'button';
      node.append(el('span', 'dept-code', departmentCodes[dept.id] || dept.name.slice(0, 2).toUpperCase()), el('h3', '', dept.name), el('p', '', dept.description || 'Зона ответственности не указана'));
      const load = el('div', 'dept-load'); load.append(el('span', '', 'Открыто задач'), el('strong', '', dept.tasks_open ?? '—'));
      node.append(load); node.addEventListener('click', () => selectDepartment(dept.id, 'tasks')); list.append(node);
    });
    const tree = clear($('#org-tree'));
    const director = state.departments.find(dept => dept.id === 'director') || state.departments.find(dept => !dept.parent_id);
    if (director) {
      const root = el('div', 'org-root');
      root.append(el('span', 'dept-code', 'EKT'), el('h3', '', director.name), el('p', '', director.description || 'Общее управление компанией'));
      tree.append(root);
    }
    const branches = el('div', 'org-branches');
    departments.forEach(dept => {
      const branch = el('button', 'org-dept'); branch.type = 'button';
      branch.append(el('span', 'dept-code', departmentCodes[dept.id] || 'EKT'), el('h3', '', dept.name), el('p', '', dept.description || 'Зона ответственности не указана'));
      const load = el('div', 'dept-load'); load.append(el('span', '', 'Открыто / всего'), el('strong', '', `${dept.tasks_open ?? '—'} / ${dept.tasks_total ?? '—'}`));
      branch.append(load, el('span', 'text-button', 'Перейти к задачам ↗')); branch.addEventListener('click', () => selectDepartment(dept.id, 'tasks')); branches.append(branch);
    });
    tree.append(branches);
  }
  function selectDepartment(id, view) { $('#department').value = id; if (view) showView(view); loadAll(); }
  function renderFocus() {
    const target = clear($('#focus-tasks'));
    const tasks = state.tasks.filter(task => task.status !== 'done').sort((a, b) => {
      const priority = task => task.status === 'blocked' ? 0 : isOverdue(task) ? 1 : task.due_date ? 2 : 3;
      return priority(a) - priority(b) || String(a.due_date || '9999').localeCompare(String(b.due_date || '9999'));
    }).slice(0, 4);
    if (!tasks.length) { empty(target, 'Всё спокойно', 'Открытых задач за этот период нет.', { label: 'Создать задачу', callback: () => openTask() }); return; }
    tasks.forEach(task => {
      const button = el('button', 'focus-task'); button.type = 'button';
      const details = el('span', 'focus-task-details');
      details.append(el('span', 'focus-title', task.title), el('span', 'focus-meta', [departmentName(task.department), task.assignee || 'Не назначен'].join(' · ')));
      button.append(el('span', `task-bullet ${safeStatus(task.status)}`), details, el('span', `focus-date${isOverdue(task) ? ' overdue' : ''}`, displayDate(task.due_date)));
      button.addEventListener('click', () => openTask(task)); target.append(button);
    });
  }
  function filteredTasks() {
    const status = $('#status-filter').value;
    const search = $('#task-search').value.trim().toLocaleLowerCase('ru');
    return state.tasks.filter(task => (status === 'all' || task.status === status) && (!search || `${task.title} ${task.description || ''} ${task.assignee || ''} ${departmentName(task.department)}`.toLocaleLowerCase('ru').includes(search)));
  }
  function renderTasks() {
    const tasks = filteredTasks();
    const board = clear($('#task-board'));
    Object.entries(statusNames).forEach(([status, name]) => {
      const column = el('section', 'board-column');
      const columnTasks = tasks.filter(task => safeStatus(task.status) === status);
      const heading = el('div', 'column-heading'); heading.append(el('span', `task-bullet ${status}`), el('span', '', name), el('span', 'count', columnTasks.length)); column.append(heading);
      if (!columnTasks.length) column.append(el('div', 'board-empty', $('#task-search').value ? 'Нет подходящих задач' : 'Здесь пока нет задач'));
      columnTasks.forEach(task => {
        const card = el('button', 'board-task'); card.type = 'button';
        card.append(el('span', 'task-dept', departmentName(task.department)), el('strong', '', task.title));
        if (task.description) card.append(el('p', '', task.description));
        const meta = el('span', 'task-card-bottom'); meta.append(el('span', 'task-card-assignee', task.assignee || 'Не назначен'), el('span', isOverdue(task) ? 'overdue' : '', displayDate(task.due_date)));
        card.append(meta); card.addEventListener('click', () => openTask(task)); column.append(card);
      });
      board.append(column);
    });
    const target = clear($('#task-table'));
    if (!tasks.length) { empty(target, 'Задач не найдено', 'Измените фильтры или создайте первую задачу.'); return; }
    const table = el('table');
    const header = el('thead'); const headerRow = el('tr');
    ['Задача', 'Департамент', 'Статус', 'Ответственный', 'Срок'].forEach(name => { const cell = el('th', '', name); cell.scope = 'col'; headerRow.append(cell); });
    header.append(headerRow); table.append(header);
    const body = el('tbody');
    tasks.forEach(task => {
      const row = el('tr'); const name = el('td'); const edit = el('button', 'table-task-title', task.title); edit.type = 'button'; edit.addEventListener('click', () => openTask(task)); name.append(edit);
      const status = el('td'); status.append(el('span', `badge badge-${safeStatus(task.status)}`, statusNames[safeStatus(task.status)]));
      row.append(name, el('td', '', departmentName(task.department)), status, el('td', '', task.assignee || 'Не назначен'), el('td', isOverdue(task) ? 'overdue' : '', displayDate(task.due_date, true))); body.append(row);
    });
    table.append(body); target.append(table);
  }
  function showView(name) {
    if (!views[name]) return;
    state.view = name;
    $$('.view').forEach(view => { view.hidden = view.id !== `view-${name}`; });
    $$('.nav [data-view]').forEach(button => { const active = button.dataset.view === name; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    $('#breadcrumb-current').textContent = views[name][0]; $('#page-title').textContent = views[name][1]; $('#page-description').textContent = views[name][2];
    $('#filters').hidden = ['content', 'catalog'].includes(name);
    $('#export-open').hidden = ['content', 'catalog'].includes(name);
    $('#new-task').hidden = ['content', 'catalog'].includes(name);
    clearTimeout(state.syncTimer);
    if (name === 'catalog') loadSync();
    try { history.replaceState(null, '', `${location.pathname}#${name}`); } catch (_) { /* View still works without browser history. */ }
  }
  function openTask(task) {
    if (!state.departments.length) { toast('Сначала загрузите список департаментов.', true); return; }
    const isEdit = Boolean(task?.id);
    $('#task-dialog-title').textContent = isEdit ? 'Изменить задачу' : 'Новая задача';
    $('#task-id').value = task?.id || '';
    $('#task-title').value = task?.title || '';
    $('#task-description').value = task?.description || '';
    $('#task-status').value = safeStatus(task?.status);
    $('#task-department').value = task?.department || ($('#department').value !== 'all' ? $('#department').value : state.departments.find(dept => dept.id === 'sales')?.id || state.departments[0].id);
    $('#task-assignee').value = task?.assignee || '';
    $('#task-due').value = task?.due_date || '';
    $('#task-error').hidden = true;
    $('#task-delete').hidden = !isEdit;
    $('#task-delete').textContent = 'Удалить';
    $('#task-delete').dataset.confirm = '';
    $('#task-dialog').showModal();
    $('#task-title').focus();
  }
  async function saveTask(event) {
    event.preventDefault();
    const id = $('#task-id').value;
    const body = { title: $('#task-title').value.trim(), description: $('#task-description').value.trim(), department: $('#task-department').value, status: $('#task-status').value, assignee: $('#task-assignee').value.trim(), due_date: $('#task-due').value || null };
    if (!body.title) { $('#task-title').setCustomValidity('Введите название задачи.'); $('#task-title').reportValidity(); return; }
    $('#task-save').disabled = true; $('#task-save').textContent = 'Сохраняем…'; $('#task-error').hidden = true;
    try {
      await api(id ? `/tasks/${encodeURIComponent(id)}` : '/tasks', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) });
      $('#task-dialog').close(); toast(id ? 'Задача обновлена' : 'Задача создана'); await loadAll();
    } catch (error) { $('#task-error').textContent = error.message; $('#task-error').hidden = false; }
    finally { $('#task-save').disabled = false; $('#task-save').textContent = 'Сохранить'; }
  }
  async function deleteTask() {
    const button = $('#task-delete');
    if (!button.dataset.confirm) { button.dataset.confirm = 'yes'; button.textContent = 'Подтвердить удаление'; return; }
    button.disabled = true;
    try { await api(`/tasks/${encodeURIComponent($('#task-id').value)}`, { method: 'DELETE' }); $('#task-dialog').close(); toast('Задача удалена'); await loadAll(); }
    catch (error) { $('#task-error').textContent = error.message; $('#task-error').hidden = false; }
    finally { button.disabled = false; }
  }
  function renderContent() {
    $('#content-welcome').value = state.content?.welcome || '';
    $('#content-delivery').value = state.content?.delivery_note || '';
    updatePreview();
    $('#content-status').textContent = state.content?.updated_at ? `Сохранено ${displayDate(state.content.updated_at)} в ${displayTime(state.content.updated_at)}` : 'Тексты загружены';
  }
  function updatePreview() { $('#welcome-preview').textContent = $('#content-welcome').value.trim() || 'Добавьте приветствие, чтобы увидеть его здесь.'; }
  async function saveContent(event) {
    event.preventDefault();
    const button = $('#content-save'); button.disabled = true; button.textContent = 'Сохраняем…';
    try {
      const body = { welcome: $('#content-welcome').value.trim(), delivery_note: $('#content-delivery').value.trim() };
      const result = await api('/content', { method: 'PUT', body: JSON.stringify(body) });
      state.content = result && typeof result.welcome === 'string' ? result : body;
      state.contentDirty = false; renderContent(); $('#content-status').textContent = 'Изменения сохранены'; toast('Тексты сохранены');
    } catch (error) { $('#content-status').textContent = error.message; toast(error.message, true); }
    finally { button.disabled = false; button.textContent = 'Сохранить тексты'; }
  }
  function openAccess() { $('#admin-token').value = state.token; if (!$('#access-dialog').open) $('#access-dialog').showModal(); }
  function syncDate(value) { return value ? `${displayDate(value, true)} · ${displayTime(value)}` : 'Ещё не было'; }
  function scheduleSyncRefresh() {
    clearTimeout(state.syncTimer);
    if (state.view === 'catalog') state.syncTimer = setTimeout(loadSync, state.sync?.running || state.sync?.queued ? 5000 : 30000);
  }
  async function loadSync() {
    if (state.syncLoading) return;
    state.syncLoading = true;
    $('#sync-refresh').disabled = true;
    try {
      state.sync = await api('/catalog-sync');
      renderSync();
    } catch (error) {
      $('#sync-error').textContent = error.message;
      $('#sync-error').hidden = false;
      $('#sync-status').textContent = 'Статус недоступен';
      $('#sync-status').className = 'badge badge-blocked';
      $('#sync-headline').textContent = 'Не удалось проверить обновление';
      $('#sync-description').textContent = 'Проверьте подключение и повторите запрос.';
      $('#sync-trigger').disabled = true;
    } finally {
      state.syncLoading = false;
      $('#sync-refresh').disabled = false;
      scheduleSyncRefresh();
    }
  }
  function renderSync() {
    const data = state.sync || {};
    const run = data.last_run || {};
    const status = data.running ? 'running' : data.queued ? 'queued' : run.status || 'idle';
    const labels = { running: 'Обновляется', queued: 'В очереди', success: 'Обновлено', partial: 'Частично обновлено', failed: 'Ошибка обновления', not_configured: 'Не подключено', cancelled: 'Остановлено', interrupted: 'Прервано', idle: 'Ожидает запуска' };
    $('#sync-status').textContent = labels[status] || 'Состояние неизвестно';
    $('#sync-status').className = `badge ${['running', 'queued'].includes(status) ? 'badge-in_progress' : status === 'success' ? 'badge-done' : ['failed', 'partial', 'interrupted', 'not_configured'].includes(status) ? 'badge-blocked' : 'badge-neutral'}`;
    const headlines = { running: 'Каталог обновляется в фоне', queued: 'Обновление поставлено в очередь', success: 'Последний запуск завершён успешно', partial: 'Часть данных требует повторного обновления', failed: 'Последнее обновление не завершилось', not_configured: 'Подключение к каталогу не настроено', cancelled: 'Обновление было остановлено', interrupted: 'Запуск прерван', idle: 'Каталог готов к обновлению' };
    $('#sync-headline').textContent = headlines[status] || 'Состояние обновления';
    $('#sync-description').textContent = data.stock_note || 'Данные товаров обновляются по очереди; полный список и свежесть остатков показаны отдельно.';
    const details = clear($('#sync-details'));
    [
      ['Последний успешный запуск', syncDate(data.last_success_at)],
      ['Полный список получен', syncDate(data.last_complete_list_at)],
      ['Следующее обновление', data.next_run ? syncDate(data.next_run) : data.next_run_at ? syncDate(data.next_run_at) : data.interval_seconds ? `Интервал: ${formatNumber(data.interval_seconds / 60)} мин` : 'Не запланировано'],
    ].forEach(([label, value]) => { const item = el('div', 'sync-detail'); item.append(el('span', '', label), el('strong', '', value)); details.append(item); });
    const coverage = clear($('#sync-coverage'));
    coverage.append(el('h3', '', 'Полнота и свежесть данных'));
    const grid = el('div', 'coverage-grid');
    [
      [data.local_products, 'Товаров в локальном каталоге'],
      [data.listed_products, 'Товаров в полученном списке'],
      [data.details_checked_last_5m, 'Детали проверены за 5 минут'],
      [data.pending_details, 'Ожидают загрузки деталей'],
    ].forEach(([value, label]) => { const item = el('div', 'coverage-item'); item.append(el('strong', '', formatNumber(value)), el('span', '', label)); grid.append(item); });
    coverage.append(grid);
    const freshness = el('p', 'sync-description-note');
    freshness.textContent = `Свежие данные за последние 5 минут: ${formatNumber(data.stock_coverage_percent)}${data.stock_coverage_percent !== null && data.stock_coverage_percent !== undefined ? '%' : ''}. Это свежесть деталей и остатков, а не полнота каталога.`;
    coverage.append(freshness);
    if (run.id) {
      const outcome = el('p', 'sync-description-note');
      outcome.textContent = `Последний запуск: обновлено деталей — ${formatNumber(run.details_updated)}, ошибок — ${formatNumber(run.details_failed)}${run.finished_at ? `. Завершён ${syncDate(run.finished_at)}.` : '.'}`;
      coverage.append(outcome);
    }
    $('#sync-error').hidden = !run.error_type;
    $('#sync-error').textContent = run.error_type ? `Причина сбоя: ${run.error_type}. Повторите обновление или проверьте подключение источника.` : '';
    $('#sync-trigger').disabled = Boolean(data.running || data.queued);
    $('#sync-trigger').textContent = data.running ? 'Обновляется…' : data.queued ? 'Запуск в очереди' : 'Обновить каталог';
    $('#sync-action-note').textContent = data.detail_budget ? `До ${formatNumber(data.detail_budget)} карточек за запуск` : 'Обновление запускается в фоне.';
  }
  async function triggerSync() {
    $('#sync-trigger').disabled = true;
    try {
      state.sync = await api('/catalog-sync/trigger', { method: 'POST' });
      renderSync();
      toast('Обновление каталога запущено');
      scheduleSyncRefresh();
    } catch (error) {
      $('#sync-error').textContent = error.message; $('#sync-error').hidden = false; $('#sync-trigger').disabled = false;
    }
  }
  async function exportReport(format) {
    $('#export-menu').hidden = true; $('#export-open').setAttribute('aria-expanded', 'false'); $('#export-open').disabled = true;
    try {
      const blob = await api(`/exports/${format}?${getFilters()}`, { binary: true });
      const url = URL.createObjectURL(blob); const link = el('a'); link.href = url; link.download = `EKT-${$('#department').value}-${localDate(new Date())}.${format}`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000); toast('Отчёт сформирован, загрузка файла началась');
    } catch (error) { toast(error.message, true); }
    finally { $('#export-open').disabled = false; }
  }

  $$('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
  $$('[data-close]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));
  $('#new-task').addEventListener('click', () => openTask());
  $('#refresh').addEventListener('click', () => state.view === 'catalog' ? loadSync() : loadAll());
  $('#sync-refresh').addEventListener('click', loadSync);
  $('#sync-trigger').addEventListener('click', triggerSync);
  $('#department').addEventListener('change', loadAll);
  $('#period').addEventListener('change', () => { $('#custom-dates').hidden = $('#period').value !== 'custom'; if ($('#period').value !== 'custom') loadAll(); });
  $('#filters').addEventListener('submit', event => { event.preventDefault(); loadAll(); });
  $('#status-filter').addEventListener('change', renderTasks);
  $('#task-search').addEventListener('input', renderTasks);
  $('#task-title').addEventListener('input', () => $('#task-title').setCustomValidity(''));
  $('#task-form').addEventListener('submit', saveTask);
  $('#task-delete').addEventListener('click', deleteTask);
  $('#content-form').addEventListener('submit', saveContent);
  ['#content-welcome', '#content-delivery'].forEach(selector => $(selector).addEventListener('input', () => { state.contentDirty = true; updatePreview(); $('#content-status').textContent = 'Есть несохранённые изменения'; }));
  $('#access-open').addEventListener('click', openAccess);
  $('#mobile-access').addEventListener('click', openAccess);
  $('#access-form').addEventListener('submit', event => {
    event.preventDefault(); state.token = $('#admin-token').value.trim();
    try { if (state.token) sessionStorage.setItem('ekt_admin_token', state.token); else sessionStorage.removeItem('ekt_admin_token'); } catch (_) { /* Keep the token in memory when storage is unavailable. */ }
    $('#access-label').textContent = state.token ? 'Ключ задан для этой сессии' : 'Настройки подключения'; $('#access-dialog').close(); loadAll(); if (state.view === 'catalog') loadSync();
  });
  $('#export-open').addEventListener('click', () => { const menu = $('#export-menu'); menu.hidden = !menu.hidden; $('#export-open').setAttribute('aria-expanded', String(!menu.hidden)); });
  $$('[data-export]').forEach(button => button.addEventListener('click', () => exportReport(button.dataset.export)));
  document.addEventListener('click', event => { if (!event.target.closest('.export-wrap')) { $('#export-menu').hidden = true; $('#export-open').setAttribute('aria-expanded', 'false'); } });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { $('#export-menu').hidden = true; $('#export-open').setAttribute('aria-expanded', 'false'); } });
  ['board', 'table'].forEach(view => $(`#${view}-switch`).addEventListener('click', () => {
    state.taskView = view;
    $('#task-board').hidden = view !== 'board'; $('#task-table').hidden = view !== 'table';
    ['board', 'table'].forEach(name => { $(`#${name}-switch`).classList.toggle('selected', name === view); $(`#${name}-switch`).setAttribute('aria-pressed', String(name === view)); });
  }));
  window.addEventListener('beforeunload', event => { if (state.contentDirty) { event.preventDefault(); event.returnValue = ''; } });
  $('#access-label').textContent = state.token ? 'Ключ задан для этой сессии' : 'Настройки подключения';
  $('#date-to').value = localDate(new Date());
  const weekAgo = new Date(); weekAgo.setDate(weekAgo.getDate() - 6); $('#date-from').value = localDate(weekAgo);
  $('#content-save').disabled = true; $('#new-task').disabled = true;
  showView(views[location.hash.slice(1)] ? location.hash.slice(1) : 'overview');
  loadAll();
})();
