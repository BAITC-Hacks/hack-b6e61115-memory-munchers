(() => {
  const key = 'skyline-products-api-url';
  // Embedding: <script src=".../shopping.js" data-api="https://api.example" data-basket-url="https://shop/basket"></script>
  const script = document.currentScript;
  let base = script?.dataset.api || 'http://localhost:5187';
  let basketUrl = script?.dataset.basketUrl || './basket.html';
  try { base = localStorage.getItem(key) || base; } catch { /* Browser storage is optional. */ }
  // Anonymous shopper identity: a signed token issued by the API, kept per API address in this browser only.
  const tokens = new Map(); let tokenRequest = null;
  const tokenKey = () => `skyline-shopper-token:${base}`;
  function storedToken() { try { return localStorage.getItem(tokenKey()) || tokens.get(base) || null; } catch { return tokens.get(base) || null; } }
  function forgetToken() { tokens.delete(base); try { localStorage.removeItem(tokenKey()); } catch { /* Storage may be disabled. */ } }
  async function shopperToken(renew = false) {
    if (renew) forgetToken();
    const existing = storedToken(); if (existing) return existing;
    if (!tokenRequest) tokenRequest = (async () => {
      const response = await fetch(`${base}/api/shopper`, { method: 'POST', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(response.status === 429 ? 'Слишком много запросов. Подождите минуту.' : `Ошибка сервера (${response.status}).`);
      const { token } = await response.json(); tokens.set(base, token);
      try { localStorage.setItem(tokenKey(), token); } catch { /* The in-memory token lasts until the page is closed. */ }
      return token;
    })().finally(() => { tokenRequest = null; });
    return tokenRequest;
  }
  function safeUrl(value) {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
    catch { return null; }
  }
  async function fetchApi(path, { method = 'GET', body, stream = false } = {}, retried = false) {
    const headers = { Accept: stream ? 'text/event-stream' : 'application/json' };
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
    let response;
    try { headers['X-Shopper-Token'] = await shopperToken();
      response = await fetch(`${base}${path}`, { method, headers,
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }) }); }
    catch { throw new Error('Нет связи с сервером. Проверьте подключение и обновите диалог перед повторной отправкой.'); }
    if (response.status === 401 && !retried) { await shopperToken(true); return fetchApi(path, { method, body, stream }, true); }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(data.detail || (response.status === 429 ? 'Слишком много запросов. Подождите минуту.' : `Ошибка сервера (${response.status}).`));
      error.code = data.title; error.status = response.status; throw error;
    }
    return response;
  }
  const api = async (path, options) => { const response = await fetchApi(path, options); return response.status === 204 ? null : response.json(); };
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; };
  const number = value => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 }).format(value);
  const price = (value, currency = '') => value == null ? 'Цена уточняется' : `${number(value)} ${currency}`.trim();
  function badge(basket) {
    if (safeUrl(basket.basketUrl)) basketUrl = basket.basketUrl;
    document.querySelectorAll('[data-basket-link]').forEach(node => { node.href = basketUrl; });
    document.querySelectorAll('[data-basket-count]').forEach(node => { node.textContent = String(basket.items.length); });
    window.dispatchEvent(new CustomEvent('basket-updated', { detail: basket }));
  }
  function stockText(product) {
    if (product.availableQuantity > 0) return `В наличии: ${number(product.availableQuantity)} ${product.unit || ''}`.trim();
    if (product.availableQuantity === 0) return 'Нет в наличии';
    return `${product.availability || 'Наличие уточняется'} · точный остаток неизвестен`;
  }
  function productCard(product) {
    const card = el('article', 'shop-product');
    const title = el('strong', '', product.name);
    const url = safeUrl(product.productUrl);
    if (url) { const link = el('a', '', product.name); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; title.replaceChildren(link); }
    card.append(title, el('span', 'shop-muted', `${product.code || 'Без кода'} · ${product.brand || product.category || ''}`),
      el('span', 'shop-price', price(product.price, product.currency)),
      el('span', `shop-stock ${product.stockStatus || ''}`, stockText(product)));
    if (product.catalogCheckedAt) card.append(el('span', 'shop-muted', `Данные каталога от ${new Date(product.catalogCheckedAt).toLocaleDateString('ru-RU')}`));
    if (product.certificates?.length) {
      const list = el('div', 'shop-certificates');
      product.certificates.forEach(certificate => { const href = safeUrl(certificate.url);
        const text = `Сертификат № ${certificate.number}${certificate.validUntil ? `, до ${new Date(certificate.validUntil).toLocaleDateString('ru-RU')}` : ''}`;
        const node = href ? el('a', '', text) : el('span', '', text);
        if (href) { node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; }
        node.title = certificate.type; list.append(node); });
      card.append(list);
    }
    if (product.documentUrls?.length) {
      const links = el('div', 'shop-document-links');
      product.documentUrls.forEach((value, index) => { const href = safeUrl(value); if (!href) return;
        const link = el('a', '', `Документ ${index + 1}`); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link); });
      card.append(links);
    }
    return card;
  }
  window.Shop = { api, fetchApi, el, number, price, badge, productCard, safeUrl, forgetToken,
    get base() { return base; }, get basketUrl() { return basketUrl; } };
  window.addEventListener('products-api-changed', event => {
    base = event.detail;
    window.dispatchEvent(new Event('shopping-api-changed'));
  });
  window.addEventListener('storage', event => { if (event.key === key && event.newValue && event.newValue !== base) {
    base = event.newValue; window.dispatchEvent(new Event('shopping-api-changed'));
  } });
  api('/api/basket').then(badge).catch(() => { /* Chat and basket pages expose recoverable connection errors. */ });
})();
