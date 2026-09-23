(() => {
  const key = 'skyline-products-api-url';
  let base = 'http://localhost:5187';
  try { base = localStorage.getItem(key) || base; } catch { /* Browser storage is optional. */ }
  function safeUrl(value) {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
    catch { return null; }
  }
  async function fetchApi(path, { method = 'GET', body, stream = false } = {}) {
    const headers = { Accept: stream ? 'text/event-stream' : 'application/json' };
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
    let response;
    try { response = await fetch(`${base}${path}`, { method, headers,
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }) }); }
    catch { throw new Error('Нет связи с сервером. Проверьте подключение и обновите диалог перед повторной отправкой.'); }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(data.detail || (response.status === 429 ? 'Слишком много запросов. Подождите минуту.' : `Ошибка сервера (${response.status}).`));
      error.code = data.title; error.status = response.status; throw error;
    }
    return response;
  }
  const api = async (path, options) => (await fetchApi(path, options)).json();
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const number = value => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 }).format(value);
  const price = (value, currency = '') => value == null ? 'Цена уточняется' : `${number(value)} ${currency}`.trim();
  function badge(basket) {
    document.querySelectorAll('[data-basket-count]').forEach(node => { node.textContent = String(basket.items.length); });
    window.dispatchEvent(new CustomEvent('basket-updated', { detail: basket }));
  }
  function productCard(product) {
    const card = el('article', 'shop-product');
    const title = el('strong', '', product.name);
    const url = safeUrl(product.productUrl);
    if (url) { const link = el('a', '', product.name); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; title.replaceChildren(link); }
    card.append(title, el('span', 'shop-muted', `${product.code || 'Без кода'} · ${product.brand || product.category || ''}`),
      el('span', 'shop-price', price(product.price, product.currency)),
      el('span', 'shop-muted', product.availableQuantity == null ? `${product.availability || 'Наличие уточняется'} · точный остаток неизвестен` : `Доступно: ${number(product.availableQuantity)} ${product.unit || ''}`));
    if (product.catalogCheckedAt) card.append(el('span', 'shop-muted', `Данные каталога от ${new Date(product.catalogCheckedAt).toLocaleDateString('ru-RU')}`));
    if (product.documentUrls?.length) {
      const links = el('div', 'shop-document-links');
      product.documentUrls.forEach((value, index) => { const href = safeUrl(value); if (!href) return;
        const link = el('a', '', `Документ ${index + 1}`); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link); });
      card.append(links);
    }
    return card;
  }
  window.Shop = { api, fetchApi, el, number, price, badge, productCard, safeUrl, get base() { return base; } };
  window.addEventListener('products-api-changed', event => {
    base = event.detail;
    window.dispatchEvent(new Event('shopping-api-changed'));
  });
  window.addEventListener('storage', event => { if (event.key === key && event.newValue && event.newValue !== base) {
    base = event.newValue; window.dispatchEvent(new Event('shopping-api-changed'));
  } });
  api('/api/basket').then(badge).catch(() => { /* Chat and basket pages expose recoverable connection errors. */ });
})();
