const DEFAULT_PRODUCTS_API_URL = 'http://localhost:5187';
const PRODUCTS_API_STORAGE_KEY = 'skyline-products-api-url';
const ui = Object.fromEntries([
  'connection-form', 'connection-fields', 'connection-label', 'api-url', 'expected-count',
  'database-count', 'missing-count', 'catalog-badge', 'catalog-status', 'refresh-button',
  'products-table', 'product-rows', 'page-size', 'range-label', 'page-label',
  'first-page', 'previous-page', 'next-page', 'last-page'
].map(id => [id, document.getElementById(id)]));
const state = { apiUrl: DEFAULT_PRODUCTS_API_URL, page: 1, pageSize: 20, totalPages: 0, loaded: false, busy: false };
const countFormat = new Intl.NumberFormat();
const priceFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

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
  const url = new URL(value.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('Enter an HTTP or HTTPS API address without credentials, a query, or a fragment.');
  }
  return url.href.replace(/\/+$/, '');
}

async function request(path, method = 'GET') {
  let response;
  try {
    response = await fetch(`${state.apiUrl}/api/products${path}`, { method, headers: { Accept: 'application/json' } });
  } catch {
    throw new Error('Could not reach the API. Check the API connection address and that the server is running. For HTTPS, trust its development certificate.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const validation = data?.errors ? Object.values(data.errors).flat().join(' ') : '';
    throw new Error(data?.detail || validation || data?.title || `The API replied with ${response.status}.`);
  }
  if (!data) throw new Error('The API returned an invalid response.');
  return data;
}

function renderCatalogStatus(catalog) {
  ui['expected-count'].textContent = countFormat.format(catalog.expectedCount);
  ui['database-count'].textContent = countFormat.format(catalog.databaseCount);
  ui['missing-count'].textContent = countFormat.format(catalog.missingCount);
  ui['catalog-badge'].textContent = catalog.isComplete ? 'Verified' : 'Count mismatch';
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
    emptyRows('No products are available. Use Check & refresh to check the catalog again.');
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
    code.textContent = `${product.code || 'No code'} · ID ${product.id}`;
    productCell.append(name, code);
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
  if (!Array.isArray(result.items)) throw new Error('The API returned an invalid product list.');
  renderProducts(result.items);
  state.page = result.page;
  state.pageSize = result.pageSize;
  state.totalPages = result.totalPages;
  state.loaded = true;
  ui['page-size'].value = String(result.pageSize);
  const first = result.items.length ? (result.page - 1) * result.pageSize + 1 : 0;
  const last = result.items.length ? first + result.items.length - 1 : 0;
  ui['range-label'].textContent = `${countFormat.format(first)}–${countFormat.format(last)} of ${countFormat.format(result.totalCount)} products`;
  ui['page-label'].textContent = result.totalPages ? `Page ${result.page} of ${result.totalPages}` : 'Page 0 of 0';
}

async function initializeCatalog() {
  if (state.busy) return;
  state.loaded = false;
  setBusy(true);
  emptyRows('Preparing the catalog…');
  ui['range-label'].textContent = 'Waiting for products';
  ui['page-label'].textContent = 'Page —';
  for (const id of ['expected-count', 'database-count', 'missing-count']) ui[id].textContent = '—';
  ui['catalog-badge'].textContent = 'Checking';
  ui['catalog-badge'].classList.remove('warning');
  notice('Checking the saved products against the catalog…', 'working');
  try {
    let catalog = await request('/status');
    renderCatalogStatus(catalog);
    if (catalog.requiresImport) {
      ui['catalog-badge'].textContent = 'Importing';
      notice(`Saving ${countFormat.format(catalog.missingCount)} missing products. This may take a moment…`, 'working');
      catalog = await request('/import', 'POST');
      renderCatalogStatus(catalog);
    }
    if (catalog.missingCount > 0) throw new Error('Some catalog products are still missing. Use Check & refresh to retry.');
    notice('Loading products…', 'working');
    await loadPage(1, state.pageSize);
    if (catalog.isComplete) {
      notice(catalog.importedCount
        ? `Added ${countFormat.format(catalog.importedCount)} products. All ${countFormat.format(catalog.expectedCount)} catalog products are ready.`
        : `All ${countFormat.format(catalog.expectedCount)} catalog products are saved. The count matches.`, 'success');
    } else {
      notice(`All catalog products are saved. The database also contains ${countFormat.format(catalog.unexpectedCount)} products outside this catalog, so the total count differs.`, 'warning');
    }
  } catch (error) {
    notice(error.message, 'error');
    ui['catalog-badge'].textContent = 'Needs attention';
    ui['catalog-badge'].classList.add('warning');
    emptyRows('Products could not be loaded. Check the message above, then use Check & refresh to retry.');
    ui['range-label'].textContent = 'Products unavailable';
  } finally { setBusy(false); }
}

async function changePage(page, pageSize = state.pageSize) {
  if (state.busy) return;
  setBusy(true);
  notice('Loading products…', 'working');
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
ui['connection-form'].addEventListener('submit', event => {
  event.preventDefault();
  if (state.busy) return;
  try { state.apiUrl = normalizeApiUrl(ui['api-url'].value); }
  catch (error) { notice(error.message, 'error'); return; }
  ui['connection-label'].textContent = state.apiUrl;
  try { localStorage.setItem(PRODUCTS_API_STORAGE_KEY, state.apiUrl); } catch { /* Storage may be disabled. */ }
  initializeCatalog();
});

try { state.apiUrl = normalizeApiUrl(localStorage.getItem(PRODUCTS_API_STORAGE_KEY) || DEFAULT_PRODUCTS_API_URL); }
catch { /* Use the default when browser storage is unavailable or contains an invalid URL. */ }
ui['api-url'].value = state.apiUrl;
ui['connection-label'].textContent = state.apiUrl;
initializeCatalog();
