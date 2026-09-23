(() => {
  const { api, el, price, number, badge, productCard, safeUrl } = window.Shop;
  const list = document.getElementById('basket-items'); const status = document.getElementById('basket-status');
  const refresh = document.getElementById('basket-refresh'); let basket, busy = false;
  function notice(text = '', error = false) { status.textContent = text; status.classList.toggle('error', error); }
  function setBusy(value) { busy = value; refresh.disabled = value; list.setAttribute('aria-busy', String(value)); list.querySelectorAll('input,button').forEach(e => { e.disabled = value; }); }
  function render(data) {
    basket = data; badge(data); list.replaceChildren(); document.getElementById('basket-count').textContent = `Позиций: ${data.items.length}`;
    if (!data.items.length) list.append(el('p', 'basket-empty', 'Корзина пока пуста. Откройте помощника в каталоге, чтобы подобрать товары.'));
    for (const item of data.items) {
      const card = productCard(item.product); card.classList.add('basket-item');
      const form = el('form', 'basket-quantity'); const label = el('label', '', `Количество ${item.product.unit ? `(${item.product.unit})` : '(единица не указана)'}`);
      const quantity = el('input'); quantity.type = 'number'; quantity.name = 'quantity'; quantity.min = item.product.minimumOrder > 0 ? String(item.product.minimumOrder) : '0.001';
      quantity.step = item.product.orderMultiple > 0 ? String(item.product.orderMultiple) : '0.001'; quantity.max = String(item.product.websiteOrderLimit ?? 1000000); quantity.required = true; quantity.value = String(item.quantity); label.append(quantity);
      const save = el('button', 'shop-primary', 'Обновить'); save.type = 'submit'; const remove = el('button', 'chat-text-button', 'Удалить'); remove.type = 'button';
      form.append(label, save, remove); card.append(form,
        el('span', 'shop-muted', `Минимум: ${item.product.minimumOrder == null ? 'не указан' : number(item.product.minimumOrder)} · Кратность: ${item.product.orderMultiple == null ? 'не указана' : number(item.product.orderMultiple)} · Лимит заказа: ${item.product.websiteOrderLimit == null ? 'не указан' : number(item.product.websiteOrderLimit)}`),
        el('strong', 'shop-price', price(item.total, item.product.currency)));
      form.addEventListener('submit', event => { event.preventDefault(); change(item.product.id, Number(quantity.value)); });
      remove.addEventListener('click', () => change(item.product.id, 0)); list.append(card);
    }
    const totals = document.getElementById('basket-totals'); totals.replaceChildren();
    Object.entries(data.totals).forEach(([currency, value]) => totals.append(el('p', 'basket-total', price(value, currency))));
    if (!Object.keys(data.totals).length) totals.append(el('p', 'basket-total', data.items.length ? 'Требуется уточнение цены' : '0'));
    document.getElementById('basket-price-note').textContent = data.hasUnknownPrices ? 'Некоторые цены неизвестны и не включены в итог.' : 'Цены приведены по данным каталога.';
    const link = document.getElementById('checkout-link'); const checkout = safeUrl(data.checkoutUrl);
    link.hidden = !checkout || !data.items.length; if (checkout) link.href = checkout;
    document.getElementById('checkout-note').hidden = Boolean(checkout) || !data.items.length;
    document.getElementById('checkout-handoff').hidden = link.hidden;
  }
  async function load() { if (busy) return; setBusy(true); notice('Загружаю корзину…'); try { render(await api('/api/basket')); notice(); } catch (error) { notice(error.message, true); } finally { setBusy(false); } }
  async function change(id, quantity) {
    if (busy) return; setBusy(true); notice('Проверяю количество…');
    try { render(await api(`/api/basket/items/${id}${quantity === 0 ? `?version=${basket.version}` : ''}`, quantity === 0
      ? { method: 'DELETE' } : { method: 'PATCH', body: { quantity, version: basket.version } })); notice('Корзина обновлена.'); }
    catch (error) { try { render(await api('/api/basket')); } catch { /* Keep the last basket visible. */ } notice(error.message, true); }
    finally { setBusy(false); }
  }
  refresh.addEventListener('click', load); window.addEventListener('shopping-api-changed', load); load();
})();
