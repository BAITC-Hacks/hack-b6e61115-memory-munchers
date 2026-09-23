const DEFAULT_PRODUCTS_API_URL = 'http://localhost:5187';
const PRODUCTS_API_STORAGE_KEY = 'skyline-products-api-url';
const ui = Object.fromEntries([
  'connection-form', 'connection-fields', 'connection-label', 'api-url', 'expected-count',
  'database-count', 'missing-count', 'catalog-badge', 'catalog-status', 'refresh-button',
  'products-table', 'product-rows', 'page-size', 'range-label', 'page-label',
  'first-page', 'previous-page', 'next-page', 'last-page'
].map(id => [id, document.getElementById(id)]));
const state = { apiUrl: DEFAULT_PRODUCTS_API_URL, page: 1, pageSize: 20, totalPages: 0, loaded: false, busy: false };
const countFormat = new Intl.NumberFormat('ru-RU');
const priceFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

function notice(message, kind = '') {
  ui['catalog-status'].textContent = message;
  ui['catalog-status'].className = `status ${kind}`;
}

function setBusy(busy) {
  state.busy = busy;
  ui['connection-fields'].disabled = busy;
  ui['refresh-button'].disabled = busy;
  ui['page-size'].disabled = busy || !state.loaded;
  ui['products-table'].setAttribute('aria-busy', String(busy));
  ui['first-page'].disabled = ui['previous-page'].disabled = busy || !state.loaded || state.page <= 1;
  ui['next-page'].disabled = ui['last-page'].disabled = busy || !state.loaded || state.page >= state.totalPages;
}

function normalizeApiUrl(value) {
  let url;
  try { url = new URL(value.trim()); }
  catch { throw new Error('Введите корректный адрес сервера, начинающийся с http:// или https://.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Введите адрес сервера с протоколом HTTP или HTTPS без логина, пароля, параметров запроса и фрагмента после #.');
  }
  return url.href.replace(/\/+$/, '');
}

async function request(path, method = 'GET') {
  let response;
  try {
    response = await fetch(`${state.apiUrl}/api/products${path}`, { method, headers: { Accept: 'application/json' } });
  } catch {
    throw new Error('Не удалось подключиться к серверу. Проверьте адрес и доступность сервера. При использовании HTTPS убедитесь, что сертификат сервера доверенный.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (data?.title === 'Catalog file not found') {
      throw new Error('Файл каталога не найден на сервере. Обратитесь к администратору.');
    }
    const messages = {
      400: 'Некорректные параметры запроса. Проверьте настройки и повторите попытку.',
      404: 'Каталог не найден. Проверьте адрес сервера.',
      422: 'Файл каталога содержит некорректные данные. Обратитесь к администратору.',
      503: 'Сервер временно недоступен. Повторите попытку позже.'
    };
    throw new Error(messages[response.status] || `Сервер вернул ошибку (код ${response.status}). Повторите попытку позже.`);
  }
  if (!data) throw new Error('Сервер вернул некорректный ответ.');
  return data;
}

function renderCatalogStatus(catalog) {
  ui['expected-count'].textContent = countFormat.format(catalog.expectedCount);
  ui['database-count'].textContent = countFormat.format(catalog.databaseCount);
  ui['missing-count'].textContent = countFormat.format(catalog.missingCount);
  ui['catalog-badge'].textContent = catalog.isComplete ? 'Проверено' : 'Количество не совпадает';
  ui['catalog-badge'].classList.toggle('warning', !catalog.isComplete);
}

function emptyRows(message) {
  const row = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = 6;
  cell.className = 'empty-cell';
  cell.textContent = message;
  row.append(cell);
  ui['product-rows'].replaceChildren(row);
}

function productLink(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function renderProducts(products) {
  if (!products.length) {
    emptyRows('Товаров пока нет. Нажмите «Проверить и обновить», чтобы проверить каталог ещё раз.');
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const product of products) {
    const row = document.createElement('tr');
    const productCell = document.createElement('td');
    const href = productLink(product.productUrl);
    const name = document.createElement(href ? 'a' : 'span');
    name.className = 'product-name';
    name.textContent = product.name;
    if (href) {
      name.href = href;
      name.target = '_blank';
      name.rel = 'noopener noreferrer';
    }
    const code = document.createElement('span');
    code.className = 'product-code';
    code.textContent = `${product.code || 'Без кода'} · ИД ${product.id}`;
    productCell.append(name, code);
    const ask = document.createElement('button');
    ask.type = 'button'; ask.className = 'ask-product'; ask.textContent = 'Спросить о товаре';
    ask.addEventListener('click', () => window.dispatchEvent(new CustomEvent('ask-product', { detail: { id: product.id, name: product.name } })));
    productCell.append(ask);
    row.append(productCell);
    const price = value => value == null ? '—' : `${priceFormat.format(value)} ${product.currency}`.trim();
    for (const [value, className] of [
      [product.brand || '—', 'product-brand'],
      [product.category || '—', 'product-category'],
      [price(product.websitePrice), 'price-cell'],
      [price(product.storePrice), 'price-cell'],
      [product.availability || '—', 'product-availability']
    ]) {
      const cell = document.createElement('td');
      cell.textContent = value;
      cell.className = className;
      if (className === 'product-category') cell.title = product.categoryPath;
      row.append(cell);
    }
    fragment.append(row);
  }
  ui['product-rows'].replaceChildren(fragment);
}

async function loadPage(page, pageSize) {
  const result = await request(`?page=${page}&pageSize=${pageSize}`);
  if (!Array.isArray(result.items)) throw new Error('Сервер вернул некорректный список товаров.');
  renderProducts(result.items);
  state.page = result.page;
  state.pageSize = result.pageSize;
  state.totalPages = result.totalPages;
  state.loaded = true;
  ui['page-size'].value = String(result.pageSize);
  const first = result.items.length ? (result.page - 1) * result.pageSize + 1 : 0;
  const last = result.items.length ? first + result.items.length - 1 : 0;
  ui['range-label'].textContent = `Товары ${countFormat.format(first)}–${countFormat.format(last)} из ${countFormat.format(result.totalCount)}`;
  ui['page-label'].textContent = result.totalPages ? `Страница ${countFormat.format(result.page)} из ${countFormat.format(result.totalPages)}` : 'Страница 0 из 0';
}

async function initializeCatalog() {
  if (state.busy) return;
  state.loaded = false;
  setBusy(true);
  emptyRows('Подготовка каталога…');
  ui['range-label'].textContent = 'Ожидание товаров';
  ui['page-label'].textContent = 'Страница —';
  for (const id of ['expected-count', 'database-count', 'missing-count']) ui[id].textContent = '—';
  ui['catalog-badge'].textContent = 'Проверка';
  ui['catalog-badge'].classList.remove('warning');
  notice('Сверка сохранённых товаров с каталогом…', 'working');
  try {
    let catalog = await request('/status');
    renderCatalogStatus(catalog);
    if (catalog.requiresImport) {
      ui['catalog-badge'].textContent = 'Импорт';
      notice(`Сохранение недостающих товаров: ${countFormat.format(catalog.missingCount)}. Это может занять некоторое время…`, 'working');
      catalog = await request('/import', 'POST');
      renderCatalogStatus(catalog);
    }
    if (catalog.missingCount > 0) throw new Error('Часть товаров из каталога ещё не сохранена. Нажмите «Проверить и обновить», чтобы повторить попытку.');
    notice('Загрузка товаров…', 'working');
    await loadPage(1, state.pageSize);
    if (catalog.isComplete) {
      notice(catalog.importedCount
        ? `Добавлено товаров: ${countFormat.format(catalog.importedCount)}. Все товары из каталога доступны (всего ${countFormat.format(catalog.expectedCount)}).`
        : `Все товары из каталога сохранены (всего ${countFormat.format(catalog.expectedCount)}). Количество совпадает.`, 'success');
    } else {
      notice(`Все товары из каталога сохранены. В базе данных также есть товары вне каталога: ${countFormat.format(catalog.unexpectedCount)}. Поэтому общее количество отличается.`, 'warning');
    }
  } catch (error) {
    notice(error.message, 'error');
    ui['catalog-badge'].textContent = 'Требует внимания';
    ui['catalog-badge'].classList.add('warning');
    emptyRows('Не удалось загрузить товары. Прочитайте сообщение выше и нажмите «Проверить и обновить», чтобы повторить попытку.');
    ui['range-label'].textContent = 'Товары недоступны';
  } finally { setBusy(false); }
}

async function changePage(page, pageSize = state.pageSize) {
  if (state.busy) return;
  setBusy(true);
  notice('Загрузка товаров…', 'working');
  try {
    await loadPage(page, pageSize);
    notice('');
  } catch (error) {
    // Keep the previous page and its matching controls available for another attempt.
    ui['page-size'].value = String(state.pageSize);
    notice(error.message, 'error');
  } finally { setBusy(false); }
}

ui['refresh-button'].addEventListener('click', initializeCatalog);
ui['page-size'].addEventListener('change', () => changePage(1, Number(ui['page-size'].value)));
ui['first-page'].addEventListener('click', () => changePage(1));
ui['previous-page'].addEventListener('click', () => changePage(state.page - 1));
ui['next-page'].addEventListener('click', () => changePage(state.page + 1));
ui['last-page'].addEventListener('click', () => changePage(state.totalPages));
ui['api-url'].addEventListener('invalid', () => {
  ui['api-url'].setCustomValidity(ui['api-url'].validity.valueMissing
    ? 'Введите адрес сервера.'
    : 'Введите корректный адрес сервера, начинающийся с http:// или https://.');
});
ui['api-url'].addEventListener('input', () => ui['api-url'].setCustomValidity(''));
ui['connection-form'].addEventListener('submit', event => {
  event.preventDefault();
  if (state.busy) return;
  try { state.apiUrl = normalizeApiUrl(ui['api-url'].value); }
  catch (error) { notice(error.message, 'error'); return; }
  ui['connection-label'].textContent = state.apiUrl;
  try { localStorage.setItem(PRODUCTS_API_STORAGE_KEY, state.apiUrl); } catch { /* Storage may be disabled. */ }
  window.dispatchEvent(new CustomEvent('products-api-changed', { detail: state.apiUrl }));
  initializeCatalog();
});

try { state.apiUrl = normalizeApiUrl(localStorage.getItem(PRODUCTS_API_STORAGE_KEY) || DEFAULT_PRODUCTS_API_URL); }
catch { /* Use the default when browser storage is unavailable or contains an invalid URL. */ }
ui['api-url'].value = state.apiUrl;
ui['connection-label'].textContent = state.apiUrl;
initializeCatalog();
