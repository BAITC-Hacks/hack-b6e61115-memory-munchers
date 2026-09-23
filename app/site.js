(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const widget = $('ekt-assistant-root');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const number = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3}).format(Number(value));
  const money = value => value == null || !Number.isFinite(Number(value)) ? 'Цена не указана' : `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(Number(value))} ₸`;
  const state = {busy: false, restoring: true, sessionReady: false, proposal: null, cart: null, products: new Map(), catalog: [], total: 0, query: '', offset: 0, limit: 24, orders: [], file: null, chatOpen: false, searchVersion: 0, content: {}, catalogLoading: false, city: 'Астана', cores: '', section: '', metal: '', only: false};
  const localImages = new Set(['050200001', '050300003', '050300004', '050300042', '050400097', '050400141']);
  const labels = {NOMINALNYY_TOK:'Номинальный ток', KOLICHESTVO_POLYUSOV:'Количество полюсов', KHARAKTERISTIKA_SRABATYVANIYA:'Характеристика срабатывания', NOMINALNOE_NAPRYAZHENIE:'Номинальное напряжение', SECHENIE_MM2:'Сечение, мм²', KOLICHESTVO_ZHIL:'Количество жил', MATERIAL_ZHILY:'Материал жилы', NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST:'Отключающая способность', STEPEN_ZASHCHITY_IP:'Степень защиты IP', PROIZVODITEL:'Производитель', BREND:'Бренд', DLINA:'Длина', TSVET:'Цвет', STRANA_PROISKHOZHDENIYA:'Страна происхождения', EDINITSY_IZMERENIYA:'Единица измерения'};
  const toolNames = {search_catalog:'Поиск в каталоге', product_details:'Характеристики и наличие', find_alternatives:'Подбор аналогов', purchase_terms:'Условия покупки', previous_requests:'Предыдущие подборы', research_task:'Исследование задачи', web_search:'Поиск технической информации', propose_cart:'Подготовка состава', catalog_search:'Поиск в каталоге', catalog_details:'Карточка товара', saved_requests:'Предыдущие подборы'};

  function safeUrl(value, local = false) {
    if (!value || typeof value !== 'string') return '';
    try {
      const url = new URL(value, location.origin);
      if (!['http:', 'https:'].includes(url.protocol)) return '';
      if (local && url.origin !== location.origin) return '';
      return url.origin === location.origin ? url.pathname + url.search + url.hash : url.href;
    } catch { return ''; }
  }
  function cartUrl(cart) { return cart?.token ? `/cart/${encodeURIComponent(cart.token)}` : safeUrl(cart?.url || cart?.cart_url, true); }
  function unit(p) { return p.unit || p.properties?.EDINITSY_IZMERENIYA || p.properties?.EDINICA_IZMERENIYA || ''; }
  function freshness(p) { const timestamp=p.stock_checked_at||p.detail_checked_at; const date=timestamp?new Date(timestamp):null; return 'Локальная база EKT'+(date&&!Number.isNaN(date.valueOf())?' · обновлено '+date.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'')+' · остатки проверим перед добавлением'; }
  function stock(p) { return p.quantity == null ? 'Наличие уточняется' : Number(p.quantity) > 0 ? `В наличии · ${number(p.quantity)}${unit(p) ? ' ' + unit(p) : ''}` : 'Нет в наличии'; }
  function imageUrl(p) { const article = String(p.article || '').replace(/_$/, ''); return localImages.has(article) ? `/assets/product-${article}.jpg` : safeUrl(p.image); }
  function imageHtml(p, detail = false) {
    const url = imageUrl(p);
    return url ? `<img src="${esc(url)}" alt="${esc(p.name)}" ${detail ? '' : 'loading="lazy"'} referrerpolicy="no-referrer">` : `<span class="product-placeholder">${icon('box')}</span>${detail ? '' : '<small>Изображение не предоставлено</small>'}`;
  }
  function entries(p) {
    const props = p.properties || p.specs || {};
    return Object.entries(props).filter(([key, value]) => value !== '' && value != null && value !== false && !/^(FILES_|MORE_|ATT_|CML2_|INSTRUCTIONS|ARTIKUL|LINK|YM_|RECOMMEND|TORGOVAYA|NOVINKA|SPETSPRED|BRAND_|IMYAKARTINKI|KRATNOST_MAKS|POKAZYVAT|SKLAD|SORT|HIT|TSVET_SAYT)/.test(key) && (!Array.isArray(value) || value.length))
      .sort(([a], [b]) => Number(!!labels[b]) - Number(!!labels[a]));
  }
  const propValue = value => Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value);
  function specs(p, count = 2) { return entries(p).slice(0, count).map(([key, value]) => `${labels[key] || key.replaceAll('_', ' ')}: ${propValue(value)}`).join(' · '); }
  function docs(p) {
    return [...(Array.isArray(p.certificates) ? p.certificates : []), ...(Array.isArray(p.documents) ? p.documents : [])].map((d, i) => ({url: safeUrl(typeof d === 'string' ? d : d.url || d.link), name: typeof d === 'string' ? `Сертификат ${i + 1}` : d.name || d.title || `Документ ${i + 1}`})).filter(d => d.url).filter((d, i, all) => all.findIndex(x => x.url === d.url) === i);
  }
  function docHtml(p) { return docs(p).map(d => `<a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${icon('file')}${esc(d.name)} ↗</a>`).join(''); }
  function remember(items) { for (const p of items || []) if (p?.id != null) state.products.set(String(p.id), p); }
  function humanError(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(x => x.msg || x.message || '').filter(Boolean).join('. ') || 'Проверьте введённые данные.';
    return detail?.message || 'Сервер не смог обработать запрос.';
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeout || 35000);
    try {
      const response = await fetch(path, {...options, credentials: 'same-origin', signal: controller.signal, headers: {Accept: 'application/json', ...options.headers}});
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(humanError(data?.detail || data?.error || `Ошибка соединения (${response.status}).`));
      if (!data || typeof data !== 'object') throw new Error('Сервер вернул неполный ответ. Повторите запрос.');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Сервер не ответил вовремя. Проверьте соединение и повторите запрос.');
      if (error instanceof TypeError) throw new Error('Нет соединения с сервером. Проверьте интернет.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  const post = (path, data) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 4500); }
  function scrollChat() { const el = $('chat-scroll'); el.scrollTo({top: el.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); }
  function openChat(focus = false) { state.chatOpen = true; $('assistant-panel').hidden = false; $('chat-launcher').hidden = true; if (focus) $('chat-input').focus(); }
  function closeChat() { state.chatOpen = false; $('assistant-panel').hidden = true; $('chat-launcher').hidden = false; }
  function updateSend() { $('send-message').disabled = state.busy || state.restoring || !state.sessionReady || (!$('chat-input').value.trim() && !state.file); }
  function setBusy(on, message = 'Проверяю каталог…') {
    state.busy = on;
    document.querySelectorAll('[data-confirm], [data-propose], [data-cancel-proposal], [data-repeat-order], [data-save-order], [data-clear-cart]').forEach(b => {
      const proposal = b.closest('.proposal-card');
      b.disabled = on || !state.sessionReady || !!(proposal && proposal.dataset.active !== 'true');
    });
    $('attach-button').disabled = on;
    $('remove-attachment').disabled = on;
    $('thinking').hidden = !on;
    $('thinking-text').textContent = message;
    $('chat-scroll').setAttribute('aria-busy', String(on));
    clearInterval(setBusy.timer);
    if (on) {
      const start = performance.now();
      $('thinking-timer').textContent = '0 с';
      setBusy.timer = setInterval(() => {
        const seconds = Math.floor((performance.now() - start) / 1000);
        $('thinking-timer').textContent = `${seconds} с`;
        if (seconds >= 8) $('thinking-text').textContent = 'Ожидаю ответ сервера…';
      }, 500);
      scrollChat();
    }
    updateSend();
  }
  function linkified(text) {
    const value = String(text || '');
    const pattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|\/cart\/[^\s)]+)\)|(https?:\/\/[^\s<>]+|\/cart\/[A-Za-z0-9_-]+)/g;
    let html = '', last = 0;
    for (const match of value.matchAll(pattern)) {
      html += esc(value.slice(last, match.index));
      let target = match[2] || match[3], suffix = '';
      if (!match[2]) { const trimmed = target.replace(/[.,;!?)]+$/, ''); suffix = target.slice(trimmed.length); target = trimmed; }
      const url = safeUrl(target);
      html += url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(match[1] || target)}</a>${esc(suffix)}` : esc(match[0]);
      last = match.index + match[0].length;
    }
    return html + esc(value.slice(last));
  }
  function addMessage(text, role = 'assistant', options = {}) {
    $('chat-welcome').hidden = true;
    const article = document.createElement('article');
    article.className = `message ${role}${options.error ? ' error' : ''}`;
    article.innerHTML = `${role === 'assistant' ? `<div class="message-avatar">${icon('spark')} EKT Ассистент</div>` : ''}<div class="message-body">${role === 'user' ? esc(text) : linkified(text)}</div>`;
    $('messages').append(article);
    return article;
  }
  function renderProduct(p, chat = false) {
    const available = p.quantity != null && Number(p.quantity) > 0;
    if (chat) return `<article class="chat-product"><div class="chat-product-meta"><span>АРТ. ${esc(p.article)}</span><span>${esc(stock(p))}</span></div><button class="chat-product-name" data-product="${esc(p.id)}">${esc(p.name)}</button>${specs(p) ? `<p class="chat-specs">${esc(specs(p))}</p>` : ''}${p.explanation ? `<p class="chat-specs">${esc(p.explanation)}</p>` : ''}<div class="document-links">${docHtml(p)}</div><div class="chat-product-bottom"><span class="price">${money(p.price)}${unit(p) ? `<small> / ${esc(unit(p))}</small>` : ''}</span><button data-product="${esc(p.id)}">${available ? 'Выбрать' : 'Подробнее'} ↗</button></div></article>`;
    return `<article class="product-card"><button class="product-visual" data-product="${esc(p.id)}" aria-label="Открыть ${esc(p.name)}">${imageHtml(p)}</button><div class="product-info"><div class="product-meta"><span>АРТ. ${esc(p.article)}</span><span>EKT</span></div><button class="product-name" data-product="${esc(p.id)}">${esc(p.name)}</button><p class="product-spec">${esc(specs(p) || p.description || 'Подробности в карточке товара')}</p><p class="stock-line${available ? '' : ' unavailable'}">${esc(stock(p))}</p><div class="product-bottom"><span class="price">${money(p.price)}${unit(p) ? `<small> / ${esc(unit(p))}</small>` : ''}</span><button class="product-buy" data-product="${esc(p.id)}" aria-label="Выбрать количество: ${esc(p.name)}" title="${available ? 'Выбрать количество' : 'Посмотреть карточку'}">${icon(available ? 'plus' : 'search')}</button></div></div></article>`;
  }
  function nativeStock(p) {
    const stores = (p.stores || []).filter(s => String(s.name || '').toLowerCase().includes(state.city.toLowerCase()));
    return stores.length ? stores.reduce((sum,s) => sum + Number(s.quantity || 0),0) : null;
  }
  function nativeCatalog() {
    if (!$('main')) return;
    const chip = (group,value) => '<button type="button" class="chip '+(state[group]===value?'on':'')+'" data-g="'+group+'" data-v="'+esc(value)+'">'+esc(value)+'</button>';
    const list=state.catalog.filter(p=>{
      const props=p.properties||{};
      return (!state.cores||String(props.KOLICHESTVO_ZHIL||'')===state.cores)&&(!state.section||String(props.SECHENIE_MM2||props.NOMINALNOE_SECHENIE_PROVODNIKA||'').replace('.',',').startsWith(state.section))&&(!state.metal||String(props.MATERIAL_ZHILY||'').toLowerCase()===state.metal.toLowerCase())&&(!state.only||(nativeStock(p)!==null&&nativeStock(p)>0));
    });
    const rows=list.map(p=>{
      const here=nativeStock(p), available=(here??Number(p.quantity))>0;
      const held=(state.cart?.items||[]).find(item=>String(item.id)===String(p.id));
      const stockText=here===null?stock(p)+' · все склады':here>0?state.city+' · '+number(here)+(unit(p)?' '+unit(p):''):'Под заказ';
      return '<article class="row"><div><div class="sku">Код товара '+esc(p.article)+'</div><h2 class="name">'+esc(p.name)+'</h2><p class="meta muted">'+esc(entries(p).slice(0,4).map(([,v])=>propValue(v)).join(' · '))+'</p><p class="stock '+(available?'':'out')+'">'+esc(stockText)+'</p></div><div class="buybox"><p class="price">'+money(p.price)+(unit(p)?'<span class="muted">/'+esc(unit(p))+'</span>':'')+'</p>'+(available?'<button class="buy" type="button" data-product="'+esc(p.id)+'">Купить'+(held?' · '+number(held.quantity):'')+'</button>':'<button class="quiet" type="button" data-alternatives="'+esc(p.article)+'">Подобрать аналог</button>')+'</div></article>';
    }).join('') || '<p class="empty">'+(state.catalogLoading?'Загружаем каталог…':'В этой выборке нет совпадений. Уточните поиск или спросите ассистента.')+'</p>';
    $('main').innerHTML='<section class="crumb" id="catalog"><div class="muted">Кабель / Провод / Кабель силовой для стационарной прокладки</div><h1>Кабель силовой для стационарной прокладки</h1><p class="muted">Медный ВВГ и ВВГнг, плюс АСБл под заказ. Цены и остатки — из локальной базы EKT.</p><p><button class="quiet" data-open-chat>Спросить ассистента</button></p></section><div class="layout"><aside><b>Фильтр по параметрам</b><div class="flabel">Количество жил</div><div class="chips">'+['2','3','5'].map(v=>chip('cores',v)).join('')+'</div><div class="flabel">Сечение, мм²</div><div class="chips">'+['1,5','2,5','4','6','120','240'].map(v=>chip('section',v)).join('')+'</div><div class="flabel">Материал жилы</div><div class="chips">'+['Медь','Алюминий'].map(v=>chip('metal',v)).join('')+'</div><label class="check"><input type="checkbox" id="only" '+(state.only?'checked':'')+' /> Только в наличии здесь</label></aside><div>'+rows+(state.catalog.length<state.total?'<p style="padding:16px 20px"><button class="quiet" id="more-products" '+(state.catalogLoading?'disabled':'')+'>Показать ещё</button></p>':'')+'</div></div><section class="terms" id="terms"><article><h2>Оплата</h2><p class="muted">Физлицам — карта онлайн или оплата при получении. Юрлицам — перечисление по счёту.</p></article><article><h2>Доставка</h2><p class="muted">'+esc(state.content.delivery_note||'Стоимость и срок зависят от города, адреса и заказа. Итоговые условия подтверждает менеджер.')+'</p></article><article><h2>Минимальная партия</h2><p class="muted">Количество зависит от упаковки и формы поставки. Кратность проверяется для выбранной позиции.</p></article></section><p class="foot">ГК Электрокомплект · Локальная база EKT · Показано '+list.length+' из '+number(state.total)+'. Цены не являются офертой. <a href="https://ekt.kz/checkout-delivery/" target="_blank" rel="noopener">Условия EKT.kz</a> · <a href="/admin">Центр управления</a></p>';
  }
  async function loadCatalog(query=state.query,append=false) {
    const version=++state.searchVersion;
    state.query=query.trim();state.catalogLoading=true;
    const offset=append?state.catalog.length:0;
    if(!append)state.catalog=[];
    nativeCatalog();
    try {
      const data=await api('/api/catalog?q='+encodeURIComponent(state.query||'ВВГ')+'&limit='+state.limit+'&offset='+offset);
      if(version!==state.searchVersion)return;
      const items=Array.isArray(data.items)?data.items:[];remember(items);
      state.catalog=append?[...state.catalog,...items.filter(p=>!state.catalog.some(old=>old.id===p.id))]:items;
      state.total=Number(data.total??state.catalog.length);
    }catch(error){if(version===state.searchVersion){toast(error.message);state.total=state.catalog.length;}}
    finally{if(version===state.searchVersion){state.catalogLoading=false;nativeCatalog();}}
  }
  async function openProduct(id) {
    const version = (openProduct.version || 0) + 1;
    openProduct.version = version;
    const dialog = $('product-dialog');
    $('product-detail').innerHTML = '<div class="catalog-state">Загружаем карточку товара…</div>';
    if (!dialog.open) dialog.showModal();
    try {
      const p = await api(`/api/catalog/${encodeURIComponent(id)}`);
      if (version !== openProduct.version) return;
      remember([p]);
      const available = p.quantity != null && Number(p.quantity) > 0;
      const fields = entries(p), source = safeUrl(p.url);
      const certificates = docHtml(p);
      $('product-detail').innerHTML = `<div class="detail-layout"><div class="detail-image">${imageHtml(p, true)}</div><div class="detail-copy"><p class="eyebrow">АРТИКУЛ ${esc(p.article)}</p><h2>${esc(p.name)}</h2><p class="stock-line${available ? '' : ' unavailable'}">${esc(stock(p))}</p><div class="price">${money(p.price)}${unit(p) ? `<small> / ${esc(unit(p))}</small>` : ''}</div>${available ? `<div class="detail-actions"><label class="quantity-field">Количество${unit(p) ? ', ' + esc(unit(p)) : ''}<input id="product-quantity" type="number" min="0.001" step="any" max="${esc(p.quantity)}" value="${Number(p.quantity) >= 1 ? 1 : esc(p.quantity)}" inputmode="decimal"></label><button class="primary-button" data-propose="${esc(p.id)}"${state.busy || !state.sessionReady ? ' disabled' : ''}>Подготовить к добавлению</button></div><p class="detail-caption">Покажем состав и сумму. Для добавления понадобится ваше подтверждение.</p>` : `<button class="primary-button" data-alternatives="${esc(p.article)}">Найти аналоги</button>`}${p.stock_warning ? `<p class="detail-warning">${esc(p.stock_warning)}</p>` : ''}</div></div><div class="detail-data"><h3>Технические характеристики</h3>${fields.length ? `<dl class="spec-list">${fields.map(([key, value]) => `<div><dt>${esc(labels[key] || key.replaceAll('_', ' '))}</dt><dd>${esc(propValue(value))}</dd></div>`).join('')}</dl>` : '<p class="detail-caption">Характеристики в доступной карточке не заполнены.</p>'}${p.description ? `<h3>Описание</h3><p class="detail-caption">${esc(p.description)}</p>` : ''}<h3>Сертификаты и документы</h3>${certificates ? `<div class="document-links">${certificates}</div>` : '<p class="detail-caption">Прямые ссылки на документы в карточке не предоставлены.</p>'}${Array.isArray(p.stores) && p.stores.length ? `<h3>Остатки по складам</h3><dl class="spec-list">${p.stores.filter(s => Number(s.quantity) > 0).map(s => `<div><dt>${esc(s.name || 'Склад ' + s.id)}</dt><dd>${number(s.quantity)}${unit(p) ? ' ' + esc(unit(p)) : ''}</dd></div>`).join('') || '<div><dt>Остатки</dt><dd>Нет в наличии</dd></div>'}</dl>` : ''}<div class="detail-source">${source ? `<a href="${esc(source)}" target="_blank" rel="noopener noreferrer">Карточка на EKT.kz ↗</a>` : ''}<span>${esc(freshness(p))}</span></div></div>`;
    } catch (error) { if (version === openProduct.version) $('product-detail').innerHTML = `<div class="catalog-state"><strong>Карточка недоступна</strong><p>${esc(error.message)}</p><button class="secondary-button" data-product="${esc(id)}">Повторить</button></div>`; }
  }
  function renderProposal(proposal, scroll = true) {
    document.querySelectorAll('.proposal-card[data-active="true"]').forEach(node => { node.removeAttribute('data-active'); node.querySelectorAll('button').forEach(b => b.disabled = true); });
    state.proposal = proposal?.status === 'pending' ? proposal : null;
    $('pending-bar').hidden = !state.proposal;
    if (!state.proposal) return;
    const p = state.proposal;
    const existing = document.getElementById(`proposal-${p.id}`);
    if (existing) { existing.dataset.active = 'true'; existing.querySelectorAll('button').forEach(b => b.disabled = state.busy); if (scroll) existing.scrollIntoView({block: 'nearest', behavior: 'smooth'}); return; }
    $('chat-welcome').hidden = true;
    const box = document.createElement('section');
    box.className = 'proposal-card'; box.id = `proposal-${p.id}`; box.dataset.active = 'true';
    const total = (p.items || []).reduce((sum, item) => sum + Number(item.quantity) * Number(item.price), 0);
    box.innerHTML = `<h3>Проверьте перед добавлением</h3><p>Этот состав ещё не добавлен в корзину.</p><ul class="proposal-items">${(p.items || []).map(item => `<li><strong>${esc(item.name)}</strong><small>АРТ. ${esc(item.article)}</small><div class="proposal-line"><span>${number(item.quantity)} × ${money(item.price)}</span><b>${money(Number(item.quantity) * Number(item.price))}</b></div></li>`).join('')}</ul><div class="proposal-total"><span>Сумма подбора</span><strong>${money(total)}</strong></div><button class="confirm-button" data-confirm="${esc(p.id)}"${state.busy ? ' disabled' : ''}>Да, добавь</button><button class="cancel-proposal" data-cancel-proposal="${esc(p.id)}"${state.busy ? ' disabled' : ''}>Отменить подбор</button><p class="proposal-note">Перед добавлением сервер проверит цену и остаток. Оформление заказа — отдельный шаг.</p>`;
    $('messages').append(box);
    if (scroll) scrollChat();
  }
  function updateCart(cart) {
    if (!cart) return;
    state.cart = cart;
    const count = (cart.items || []).length;
    $('cart-count').textContent = String(count); $('cart-count').hidden = !count;
    if ($('cartn')) $('cartn').textContent = String(count);
    $('chat-cart-bar').hidden = !count;
    $('chat-cart-summary').textContent = `${count} поз. · ${money(cart.total)}`;
    const url = cartUrl(cart); $('chat-cart-link').href = url || '#';
    if ($('cart-dialog').open) renderCart();
    if (state.catalog.length) nativeCatalog();
  }
  async function refreshCart() { const cart = await api('/api/cart'); updateCart(cart); return cart; }
  async function refreshSession() {
    const session = await api('/api/session');
    state.orders = Array.isArray(session.orders) ? session.orders : []; $('orders-count').textContent = state.orders.length ? ` · ${state.orders.length}` : '';
    if (session.cart) updateCart(session.cart);
    renderProposal(session.proposal, false);
    return session;
  }
  async function applyReply(data) {
    let narrative = data.text || data.reply || (data.status === 'confirmed' ? 'Товары добавлены в корзину.' : 'Запрос обработан.');
    // API text also includes complete plaintext product cards for Telegram.
    // In the web chat, those same verified records are rendered as compact cards.
    const firstProduct = Array.isArray(data.products) && data.products[0];
    if (firstProduct?.name) {
      const boundary = narrative.indexOf('\n\n' + firstProduct.name);
      if (boundary >= 0) narrative = narrative.slice(0, boundary).trim();
      else if (narrative.startsWith(firstProduct.name + '\nАртикул:')) narrative = '';
    }
    const article = addMessage(narrative || 'Данные из каталога EKT:');
    remember(data.products || []);
    if (Array.isArray(data.products) && data.products.length) article.insertAdjacentHTML('beforeend', data.products.map(p => renderProduct(p, true)).join(''));
    if (Array.isArray(data.trace) && data.trace.length) article.insertAdjacentHTML('beforeend', `<details class="tool-trace"><summary>Выполнено действий: ${data.trace.length}</summary><ol>${data.trace.map(t => { const name = typeof t === 'string' ? t : t.name || t.tool || 'Проверка данных'; return `<li>${icon('check')}${esc(toolNames[name] || name)}</li>`; }).join('')}</ol></details>`);
    if (data.seconds != null || data.manager) article.insertAdjacentHTML('beforeend', `<div class="message-meta">${data.seconds != null ? `<span>${number(data.seconds)} с</span>` : ''}${data.manager ? '<span class="manager-badge">Помощь с выбором</span>' : ''}</div>`);
    if (data.attachment_warning) article.insertAdjacentHTML('beforeend', `<p class="detail-warning">${esc(data.attachment_warning)}</p>`);
    if (data.proposal) renderProposal(data.proposal, false);
    if (data.cart) updateCart(data.cart);
    try { await refreshSession(); } catch { if (data.status === 'confirmed') await refreshCart().catch(() => {}); }
    scrollChat();
  }
  async function send(text, file = state.file) {
    text = String(text || '').trim();
    if (state.busy || state.restoring || !state.sessionReady || (!text && !file)) return;
    openChat();
    addMessage([text, file ? `📎 ${file.name}` : ''].filter(Boolean).join('\n'), 'user');
    $('chat-input').value = ''; $('chat-input').style.height = ''; clearAttachment();
    setBusy(true, file ? 'Читаю файл и проверяю каталог…' : 'Проверяю каталог…');
    try {
      let data;
      if (file) { const form = new FormData(); form.append('file', file); form.append('text', text); data = await api('/api/attachment', {method: 'POST', body: form}); }
      else data = await post('/api/chat', {text});
      await applyReply(data);
    } catch (error) {
      const message = addMessage(error.message, 'assistant', {error: true});
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Повторить запрос';
      retry.addEventListener('click', () => { if (!state.busy) { retry.disabled = true; send(text, file); } }); message.append(retry);
      await refreshSession().catch(() => {});
      scrollChat();
    } finally { setBusy(false); }
  }
  async function proposeProduct(id) {
    if (state.busy || !state.sessionReady) return;
    const field = $('product-quantity'), quantity = Number(field?.value);
    if (!field || !field.checkValidity() || !Number.isFinite(quantity) || quantity <= 0) { field?.reportValidity(); toast('Укажите количество в пределах остатка.'); return; }
    const product = state.products.get(String(id));
    $('product-dialog').close(); openChat(); setBusy(true, 'Проверяю состав и количество…');
    addMessage(`Подготовить к добавлению: ${product?.name || 'товар'} (${product?.article || id}), количество ${number(quantity)}.`, 'user');
    try {
      const data = await post('/api/proposal', {items: [{product_id: Number(id), quantity}]});
      const proposal = data.proposal || data;
      if (proposal.status !== 'pending') throw new Error(data.text || 'Не удалось подготовить состав.');
      renderProposal(proposal); await refreshCart();
    } catch (error) { addMessage(error.message, 'assistant', {error: true}); scrollChat(); }
    finally { setBusy(false); }
  }
  async function confirm(id) {
    if (state.busy || !state.sessionReady || state.proposal?.id !== id) return;
    setBusy(true, 'Проверяю остатки перед добавлением…');
    try {
      const data = await post('/api/confirm', {proposal_id: id, confirmation: 'Да, добавь'});
      if (data.status === 'confirmed') {
        const card = document.getElementById(`proposal-${id}`); card?.classList.add('confirmed');
        if (card) { card.removeAttribute('data-active'); card.querySelector('[data-confirm]').textContent = 'Добавлено в корзину'; card.querySelectorAll('button').forEach(b => b.disabled = true); }
        state.proposal = null; $('pending-bar').hidden = true;
      }
      await applyReply(data);
      await refreshCart();
    } catch (error) {
      addMessage(`${error.message}\nПроверяем корзину: запрос мог уже выполниться.`, 'assistant', {error: true});
      await Promise.allSettled([refreshCart(), refreshSession()]);
      scrollChat();
    } finally { setBusy(false); document.querySelectorAll('.proposal-card:not([data-active="true"]) button').forEach(b => b.disabled = true); }
  }
  async function cancelProposal() {
    if (state.busy || !state.proposal) return;
    setBusy(true, 'Отменяю неподтверждённый подбор…');
    try { await post('/api/proposal/cancel', {}); renderProposal(null); addMessage('Подбор отменён. Корзина не менялась.'); }
    catch (error) { addMessage(error.message, 'assistant', {error: true}); }
    finally { setBusy(false); document.querySelectorAll('.proposal-card:not([data-active="true"]) button').forEach(b => b.disabled = true); scrollChat(); }
  }
  function renderCart() {
    const cart = state.cart, items = cart?.items || [];
    $('cart-detail').innerHTML = items.length ? `${items.map(p => `<article class="cart-line"><h3>${esc(p.name)}</h3><small>АРТ. ${esc(p.article)}</small><div><span>${number(p.quantity)} × ${money(p.price)}</span><strong>${money(Number(p.quantity) * Number(p.price))}</strong></div></article>`).join('')}<div class="cart-total"><span>Итого</span><strong>${money(cart.total)}</strong></div>${cartUrl(cart) ? `<a class="primary-button" href="${esc(cartUrl(cart))}" target="_blank" rel="noopener">Открыть актуальную корзину ↗</a>` : ''}<button class="secondary-button cart-save" data-save-order${state.busy ? ' disabled' : ''}>Сохранить подбор для повторного заказа</button><button class="cart-clear" data-clear-cart${state.busy ? ' disabled' : ''}>Очистить корзину</button><p class="cart-note">Корзина ассистента. Товары не зарезервированы. Оформление и оплата на EKT.kz пока не подключены.</p>` : `<div class="empty-cart">${icon('cart')}<h3>Пока пусто</h3><p>Подберите товары в каталоге или в чате.<br>Перед добавлением вы увидите состав и сумму.</p><button class="secondary-button" data-cart-start>Начать подбор</button></div>`;
  }
  async function openCart() {
    $('cart-detail').innerHTML = '<div class="catalog-state">Проверяем корзину…</div>';
    if (!$('cart-dialog').open) $('cart-dialog').showModal();
    try { await refreshCart(); renderCart(); }
    catch (error) { $('cart-detail').innerHTML = `<div class="catalog-state"><strong>Не удалось загрузить корзину</strong><p>${esc(error.message)}</p><button class="secondary-button" data-retry-cart>Повторить</button></div>`; }
  }
  async function saveOrder() {
    if (state.busy) return;
    setBusy(true, 'Сохраняю подбор…');
    try { const data = await post('/api/orders/save', {}); if (data.status === 'empty_cart') throw new Error('Корзина пуста.'); await refreshSession(); toast('Подбор сохранён. Его можно повторить через «Подборы».'); }
    catch (error) { toast(error.message); }
    finally { setBusy(false); }
  }
  async function clearCart() {
    if (state.busy) return;
    const button = document.querySelector('[data-clear-cart]');
    if (button.dataset.ready !== 'true') { button.dataset.ready = 'true'; button.textContent = 'Да, очистить корзину'; return; }
    setBusy(true, 'Очищаю корзину…');
    try { await api('/api/cart', {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirmation: 'Да, очистить корзину'})}); await refreshCart(); await refreshSession(); toast('Корзина очищена.'); }
    catch (error) { toast(error.message); }
    finally { setBusy(false); }
  }
  async function showOrders() {
    openChat(); if (state.busy) return;
    setBusy(true, 'Читаю сохранённые подборы…');
    try {
      await refreshSession();
      const article = addMessage(state.orders.length ? 'Сохранённые подборы. При повторе проверим актуальные цены и наличие и снова попросим подтверждение.' : 'Сохранённых подборов пока нет. Соберите корзину и нажмите «Сохранить подбор» — я смогу найти её в следующий раз.');
      for (const order of state.orders) {
        const snapshot = order.snapshot || order;
        const items = snapshot.items || order.items || [];
        const date = order.created_at ? new Date(order.created_at) : null;
        article.insertAdjacentHTML('beforeend', `<section class="saved-order"><strong>${date && !Number.isNaN(date.valueOf()) ? 'Подбор от ' + date.toLocaleDateString('ru-RU') : 'Сохранённый подбор'}</strong><p>${items.map(p => `${esc(p.name)} × ${number(p.quantity)}`).join('<br>')}</p><button class="secondary-button" data-repeat-order="${esc(order.id)}">Повторить подбор ↗</button></section>`);
      }
      scrollChat();
    } catch (error) { addMessage(error.message, 'assistant', {error: true}); scrollChat(); }
    finally { setBusy(false); }
  }
  async function repeatOrder(id) {
    if (state.busy) return;
    setBusy(true, 'Проверяю актуальные данные подбора…');
    try { const data = await post(`/api/orders/${encodeURIComponent(id)}/repeat`, {}); if (data.text) addMessage(data.text); const proposal = data.proposal || data; if (proposal.status === 'pending') renderProposal(proposal); else if (!data.text) throw new Error(data.detail || 'Этот подбор нельзя повторить. Проверьте наличие товаров.'); }
    catch (error) { addMessage(error.message, 'assistant', {error: true}); }
    finally { setBusy(false); scrollChat(); }
  }
  function fillPrompt(text, hint) { openChat(true); $('chat-input').value = text; $('chat-input').placeholder = hint || 'Спросите о товаре или опишите задачу…'; $('chat-input').setSelectionRange(text.length, text.length); updateSend(); }
  function workflow(name) {
    if (state.busy) { toast('Дождитесь текущего ответа.'); openChat(); return; }
    if (name === 'article') fillPrompt('Проверь наличие, характеристики и сертификат артикула ', 'Введите артикул');
    else if (name === 'analogs') fillPrompt('Подбери аналог товара ', 'Введите артикул или название');
    else if (name === 'help') fillPrompt('Помоги выбрать товар для ', 'Опишите, что нужно сделать');
    else if (name === 'repeat') showOrders();
    else if (name === 'terms') { send('Какие условия оплаты, доставки и минимальной партии?'); }
    else if (name === 'specification') { openChat(); $('attachment-input').click(); }
  }
  function clearAttachment() { state.file = null; $('attachment-input').value = ''; $('attachment-chip').hidden = true; updateSend(); }
  function chooseAttachment(file) {
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) { toast('Максимальный размер файла — 15 МБ.'); clearAttachment(); return; }
    if (!/\.(xlsx|docx|pdf|jpe?g|png)$/i.test(file.name)) { toast('Поддерживаются Excel .xlsx, Word .docx, PDF, JPEG и PNG.'); clearAttachment(); return; }
    state.file = file; $('attachment-name').textContent = file.name; $('attachment-chip').hidden = false; updateSend(); openChat(); $('chat-input').focus();
  }
  async function restore() {
    state.restoring = true; updateSend(); $('session-error').hidden = true;
    try {
      const session = await api('/api/session');
      state.sessionReady = true;
      $('messages').innerHTML = '';
      state.proposal = null;
      for (const message of session.history || []) if (['user', 'assistant'].includes(message.role) && message.content) addMessage(message.content, message.role);
      state.orders = Array.isArray(session.orders) ? session.orders : []; $('orders-count').textContent = state.orders.length ? ` · ${state.orders.length}` : '';
      if (session.cart) updateCart(session.cart);
      renderProposal(session.proposal, false);
      if (!(session.history || []).length && !state.proposal) $('chat-welcome').hidden = false;
      if ((session.history || []).length || state.proposal) requestAnimationFrame(scrollChat);
    } catch (error) {
      state.sessionReady = false;
      $('session-error').innerHTML = `Не удалось восстановить диалог. ${esc(error.message)} <button id="retry-session">Повторить</button>`; $('session-error').hidden = false;
    } finally { state.restoring = false; updateSend(); }
  }
  async function health() {
    try {
      const data = await api('/health');
      const status = $('connection-status');
      status.className = data.openai_configured ? 'connected' : '';
      status.innerHTML = `<i></i>${data.openai_configured ? 'На связи · OpenAI' : 'Каталог доступен · AI не подключён'}`;
    } catch { $('connection-status').className = 'offline'; $('connection-status').innerHTML = '<i></i> Нет связи с сервером'; }
  }
  async function content() {
    try { const data = await api('/api/content'); state.content = data.content || data; const welcome = state.content.welcome; if (typeof welcome === 'string' && welcome.trim()) $('welcome-text').textContent = welcome; if (state.catalog.length) nativeCatalog(); }
    catch { /* Static welcome contains no dynamic claims and remains usable. */ }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('button, a'); if (!button || button.disabled) return;
    if (button.hasAttribute('data-open-chat')) { event.preventDefault(); openChat(true); }
    else if (button.dataset.close) $(button.dataset.close).close();
    else if (button.dataset.workflow) workflow(button.dataset.workflow);
    else if (button.dataset.g && ['cores', 'section', 'metal'].includes(button.dataset.g)) { state[button.dataset.g] = state[button.dataset.g] === button.dataset.v ? '' : button.dataset.v; nativeCatalog(); }
    else if (button.dataset.product) openProduct(button.dataset.product);
    else if (button.dataset.propose) proposeProduct(button.dataset.propose);
    else if (button.dataset.confirm) confirm(button.dataset.confirm);
    else if (button.dataset.cancelProposal) cancelProposal();
    else if (button.dataset.alternatives) { $('product-dialog').close(); send(`Подбери релевантные аналоги артикула ${button.dataset.alternatives}. Объясни, какие характеристики совпадают и чем отличаются.`); }
    else if (button.hasAttribute('data-retry-catalog')) loadCatalog();
    else if (button.hasAttribute('data-search-help')) fillPrompt(`Помоги подобрать ${state.query}`);
    else if (button.hasAttribute('data-retry-cart')) openCart();
    else if (button.hasAttribute('data-cart-start')) { $('cart-dialog').close(); openChat(true); }
    else if (button.hasAttribute('data-save-order')) saveOrder();
    else if (button.hasAttribute('data-clear-cart')) clearCart();
    else if (button.dataset.repeatOrder) repeatOrder(button.dataset.repeatOrder);
    else if (button.id === 'retry-session') { restore(); health(); }
    else if (button.id === 'more-products' && !state.catalogLoading) loadCatalog(state.query, true);
  });
  document.addEventListener('error', event => { const img = event.target; if (img.tagName === 'IMG' && img.closest('.product-visual, .detail-image')) img.outerHTML = `<span class="product-placeholder">${icon('box')}</span>`; }, true);
  let searchTimer;
  $('q')?.addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadCatalog(event.target.value), 250); });
  $('home')?.addEventListener('click', event => { event.preventDefault(); if ($('q')) $('q').value = ''; state.cores = state.section = state.metal = ''; state.only = false; loadCatalog(''); });
  $('cartbtn')?.addEventListener('click', event => { event.preventDefault(); openCart(); });
  if ($('city')) {
    $('city').innerHTML = ['Астана', 'Алматы', 'Караганда', 'Шымкент'].map(city => `<option value="${city}">${city}</option>`).join('');
    $('city').addEventListener('change', event => { state.city = event.target.value; nativeCatalog(); });
  }
  $('main')?.addEventListener('change', event => { if (event.target.id === 'only') { state.only = event.target.checked; nativeCatalog(); } });
  $('close-chat').addEventListener('click', closeChat);
  $('open-cart').addEventListener('click', openCart);
  $('history-button').addEventListener('click', showOrders);
  $('show-proposal').addEventListener('click', () => { if (state.proposal) document.getElementById(`proposal-${state.proposal.id}`)?.scrollIntoView({behavior: 'smooth', block: 'center'}); });
  $('chat-cart-link').addEventListener('click', event => { if (!cartUrl(state.cart)) { event.preventDefault(); openCart(); } });
  $('chat-form').addEventListener('submit', event => { event.preventDefault(); send($('chat-input').value); });
  $('chat-input').addEventListener('input', () => { $('chat-input').style.height = ''; $('chat-input').style.height = Math.min($('chat-input').scrollHeight, 112) + 'px'; updateSend(); });
  $('chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && innerWidth > 600) { event.preventDefault(); send($('chat-input').value); } });
  $('attach-button').addEventListener('click', () => $('attachment-input').click());
  $('attachment-input').addEventListener('change', event => chooseAttachment(event.target.files[0]));
  $('remove-attachment').addEventListener('click', clearAttachment);
  $('chat-form').addEventListener('dragover', event => event.preventDefault());
  $('chat-form').addEventListener('drop', event => { event.preventDefault(); if (!state.busy) chooseAttachment(event.dataTransfer.files[0]); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && state.chatOpen && !$('cart-dialog').open && !$('product-dialog').open) closeChat(); });
  widget.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } }));
  closeChat();
  nativeCatalog();
  // Establish the session cookie first, before requests that depend on its identity.
  restore().then(() => { health(); content(); loadCatalog(); });
})();
