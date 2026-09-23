(() => {
  const { api, fetchApi, el, price, number, badge, productCard } = window.Shop;
  const launcher = el('button', 'chat-launcher', '✦ Помощник');
  launcher.type = 'button'; launcher.setAttribute('aria-haspopup', 'dialog'); launcher.setAttribute('aria-controls', 'product-chat');
  const dialog = el('dialog', 'chat-dialog'); dialog.id = 'product-chat'; dialog.setAttribute('aria-labelledby', 'chat-title');
  const header = el('header', 'chat-header');
  const heading = el('div'); const title = el('h2', '', 'Помощник по товарам'); title.id = 'chat-title';
  heading.append(title, el('span', 'shop-muted', 'Подбор, характеристики и корзина'));
  const close = el('button', 'chat-icon-button', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Закрыть чат');
  header.append(heading, close);
  const toolbar = el('div', 'chat-toolbar');
  const all = el('button', 'chat-text-button', '← Все диалоги'); all.type = 'button';
  const reset = el('button', 'chat-text-button', 'Новый диалог'); reset.type = 'button';
  const refresh = el('button', 'chat-text-button', 'Обновить'); refresh.type = 'button';
  const basketLink = el('a', '', 'Корзина →'); basketLink.href = './basket.html'; toolbar.append(all, reset, refresh, basketLink);
  const sessions = el('div', 'chat-sessions'); sessions.setAttribute('role', 'list'); sessions.setAttribute('aria-label', 'Ранее начатые диалоги');
  const history = el('div', 'chat-history'); history.setAttribute('role', 'log'); history.setAttribute('aria-label', 'История консультации'); history.setAttribute('aria-live', 'polite');
  const status = el('div', 'chat-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const context = el('div', 'chat-context'); context.hidden = true;
  const files = el('div', 'chat-files');
  const form = el('form', 'chat-composer');
  const input = el('textarea'); input.placeholder = 'Спросите о товаре или подборе…'; input.rows = 2; input.maxLength = 8000; input.setAttribute('aria-label', 'Сообщение помощнику');
  const controls = el('div', 'chat-composer-actions');
  const attach = el('button', 'chat-text-button', '＋ Файл'); attach.type = 'button';
  const fileInput = el('input'); fileInput.type = 'file'; fileInput.multiple = true; fileInput.hidden = true; fileInput.accept = '.jpg,.jpeg,.pdf,.doc,.docx,.xls,.xlsx';
  const send = el('button', 'shop-primary', 'Отправить'); send.type = 'submit'; controls.append(attach, el('span', 'shop-muted', 'До 3 файлов · 5 МБ каждый'), send);
  form.append(input, controls, fileInput);
  const footnote = el('p', 'chat-footnote', 'Добавление в корзину — только после вашего подтверждения. Цены и доступность — по данным каталога.');
  dialog.append(header, toolbar, sessions, history, context, files, status, form, footnote);
  document.body.append(launcher, dialog);
  let sessionId = null, busy = false, selectedProduct = null, uploaded = [], pending = null, snapshot = null, opening = null, connectionEpoch = 0, view = 'list';
  function setStatus(text = '', error = false) { status.textContent = text; status.classList.toggle('error', error); }
  function setBusy(value) { busy = value; send.disabled = reset.disabled = refresh.disabled = attach.disabled = all.disabled = value; input.disabled = value;
    history.setAttribute('aria-busy', String(value)); document.querySelectorAll('.chat-proposal button').forEach(b => { b.disabled = value; }); }
  function scroll() { history.scrollTop = history.scrollHeight; }
  function message(role, text, attachmentNames = []) {
    const bubble = el('div', `chat-message ${role}`); bubble.append(el('span', 'chat-message-label', role === 'user' ? 'Вы' : 'Помощник'), el('div', 'chat-message-text', text));
    attachmentNames.forEach(name => bubble.append(el('span', 'chat-file-name', `📎 ${name}`))); history.append(bubble); return bubble;
  }
  function welcome() {
    history.replaceChildren(); message('assistant', 'Здравствуйте! Помогу разобраться в характеристиках, подобрать товар или подготовить корзину. Что ищете?');
    const prompts = el('div', 'chat-prompts');
    ['Помогите подобрать автоматический выключатель', 'Как проверить наличие товара?', 'Какие условия покупки?'].forEach(text => {
      const button = el('button', 'chat-prompt', text); button.type = 'button'; button.addEventListener('click', () => { input.value = text; input.focus(); }); prompts.append(button);
    }); history.append(prompts);
  }
  function renderFiles() {
    files.replaceChildren();
    uploaded.forEach(file => { const chip = el('span', 'chat-file-chip', file.name); const remove = el('button', 'chat-icon-button', '×'); remove.type = 'button';
      remove.setAttribute('aria-label', `Убрать ${file.name}`); remove.disabled = busy; remove.addEventListener('click', () => { uploaded = uploaded.filter(f => f.id !== file.id); renderFiles(); }); chip.append(remove); files.append(chip); });
  }
  function renderProposal(proposal) {
    const card = el('section', 'chat-proposal');
    const labels = { pending: 'Подтвердите добавление', confirmed: 'Добавлено в корзину', cancelled: 'Добавление отменено', expired: 'Предложение истекло', stale: 'Нужно обновить предложение', superseded: 'Заменено новым предложением' };
    card.append(el('strong', '', labels[proposal.status] || proposal.status));
    proposal.lines.forEach(line => {
      card.append(el('p', '', `${line.product.name} — ${proposal.status === 'confirmed' ? 'добавлено' : 'добавить'} ${number(line.quantity)} ${line.product.unit || '(единица не указана)'}`),
        el('span', 'shop-muted', `${proposal.status === 'confirmed' ? 'Количество после добавления' : 'В корзине будет'}: ${number(line.resultingQuantity)} · ${price(line.product.price, line.product.currency)} за единицу · ${line.product.availability}`),
        el('span', 'shop-muted', `Стоимость добавления: ${price(line.product.price == null ? null : line.product.price * line.quantity, line.product.currency)}`));
      if (line.product.availableQuantity == null) card.append(el('span', 'shop-muted', 'Точный остаток не подтверждён.'));
    });
    if (proposal.status === 'pending') {
      card.append(el('small', 'shop-muted', `Действует до ${new Date(proposal.expiresAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`));
      const actions = el('div', 'chat-proposal-actions');
      const confirm = el('button', 'shop-primary', 'Подтвердить добавление'); confirm.type = 'button';
      const cancel = el('button', 'chat-text-button', 'Отмена'); cancel.type = 'button';
      confirm.addEventListener('click', () => proposalAction(proposal.id, 'confirm')); cancel.addEventListener('click', () => proposalAction(proposal.id, 'cancel'));
      actions.append(confirm, cancel); card.append(actions);
    } else if (['expired', 'stale'].includes(proposal.status)) {
      const update = el('button', 'chat-text-button', 'Обновить предложение'); update.type = 'button'; update.addEventListener('click', () => proposalAction(proposal.id, 'refresh')); card.append(update);
    } else if (proposal.status === 'confirmed') { const link = el('a', '', 'Перейти в корзину →'); link.href = './basket.html'; card.append(link); }
    history.append(card);
  }
  function render(data) {
    snapshot = data; history.replaceChildren(); if (!data.messages.length) welcome();
    const renderedRuns = new Set();
    for (const item of data.messages) {
      message(item.role, item.text, item.attachments);
      if (item.role !== 'assistant') continue;
      renderedRuns.add(item.runId); renderProducts(item.runId);
    }
    for (const event of data.events) if (event.runId && !renderedRuns.has(event.runId)) { renderedRuns.add(event.runId); renderProducts(event.runId); }
    data.proposals.forEach(renderProposal); badge(data.basket);
    const latest = data.runs.at(-1);
    if (latest?.status === 'Running') setStatus('Ответ ещё готовится. Нажмите «Обновить» через несколько секунд.');
    else if (latest && ['Failed', 'Cancelled'].includes(latest.status)) setStatus('Последний запрос не завершён. Черновик можно отправить заново.', true);
    scroll();
    function renderProducts(runId) {
      const products = new Map();
      data.events.filter(e => e.runId === runId).forEach(event => {
        const list = Array.isArray(event.payload) ? event.payload : event.payload.candidates?.map(c => c.product) || [];
        list.forEach(p => products.set(p.id, p));
      });
      if (!products.size) return;
      const group = el('div', 'chat-product-cards');
      [...products.values()].slice(0, 8).forEach(p => {
        const card = productCard(p); const choose = el('button', 'chat-text-button', 'Выбрать количество'); choose.type = 'button';
        choose.addEventListener('click', () => { selectedProduct = p.id; context.textContent = `Товар: ${p.name}`; context.hidden = false;
          input.value = `Хочу добавить товар ${p.code || p.name}. Количество: `; input.focus(); }); card.append(choose); group.append(card);
      }); history.append(group);
    }
  }
  async function ensureSession() {
    if (sessionId) return sessionId;
    if (!opening) opening = api('/api/product-chat/sessions', { method: 'POST' }).then(data => { sessionId = data.id; return sessionId; }).finally(() => { opening = null; });
    return opening;
  }
  function setView(next) { view = next; dialog.classList.toggle('chat-listing', next === 'list'); all.hidden = next === 'list'; }
  function clearDraft() { pending = null; uploaded = []; selectedProduct = null; context.hidden = true; input.value = ''; renderFiles(); }
  function questions(count) { const n10 = count % 10, n100 = count % 100;
    return `${count} ${n10 === 1 && n100 !== 11 ? 'вопрос' : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? 'вопроса' : 'вопросов'}`; }
  function startNew() { sessionId = null; snapshot = null; clearDraft(); setView('chat'); welcome(); setStatus(''); }
  async function select(id) {
    if (busy) return; sessionId = id; snapshot = null; clearDraft(); setView('chat'); setStatus('Загружаю диалог…');
    try { await reload(); if (view === 'chat') setStatus(''); } catch (error) { setStatus(error.message, true); }
    if (view === 'chat') input.focus();
  }
  async function showList() {
    setView('list'); sessions.replaceChildren(el('p', 'shop-muted', 'Загружаю диалоги…'));
    const items = await api('/api/product-chat/sessions');
    if (view !== 'list') return;
    if (!items.length) { startNew(); return; }
    sessions.replaceChildren(el('div', 'chat-sessions-title', 'Ваши диалоги'));
    items.forEach(item => {
      const button = el('button', 'chat-session'); button.type = 'button'; button.setAttribute('role', 'listitem');
      if (item.id === sessionId) button.setAttribute('aria-current', 'true');
      const when = new Date(item.updatedAt).toLocaleString('ru-RU', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
      button.append(el('strong', '', item.preview || item.title || 'Диалог'), el('span', 'shop-muted', `${when} · ${questions(item.messageCount)}`));
      button.addEventListener('click', () => select(item.id)); sessions.append(button);
    });
  }
  async function reload() {
    if (!sessionId) { welcome(); return; }
    try { render(await api(`/api/product-chat/sessions/${sessionId}`)); }
    catch (error) { if (error.status === 404) { sessionId = null; snapshot = null; await showList(); setStatus('Диалог не найден. Выберите другой или начните новый.', true); } else throw error; }
  }
  function focusView() { (view === 'list' ? sessions.querySelector('.chat-session') || reset : input).focus(); }
  async function open() {
    if (!dialog.open) dialog.showModal();
    launcher.setAttribute('aria-expanded', 'true');
    if (!busy) { setStatus(''); try { if (view === 'list') await showList(); else if (sessionId) await reload(); } catch (error) { setStatus(error.message, true); } }
    focusView();
  }
  async function proposalAction(id, action) {
    if (busy) return; setBusy(true); setStatus(action === 'confirm' ? 'Проверяю и обновляю корзину…' : 'Обновляю предложение…');
    try { const result = await api(`/api/basket/proposals/${id}/${action}`, { method: 'POST' }); if (action === 'confirm') badge(result);
      await reload(); setStatus(action === 'confirm' ? 'Товары добавлены. Можно перейти в корзину.' : ''); }
    catch (error) { try { await reload(); } catch { /* Preserve existing cards for recovery. */ } setStatus(error.message, true); }
    finally { setBusy(false); }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || (!input.value.trim() && !uploaded.length)) return;
    const text = input.value.trim() || 'Помогите подобрать товары по вложению.';
    setBusy(true); setStatus('Отправляю…'); const epoch = connectionEpoch;
    try {
      await ensureSession();
      const sameRequest = pending?.message === text && pending.productId === selectedProduct && JSON.stringify(pending.attachmentIds) === JSON.stringify(uploaded.map(f => f.id));
      const body = { message: text, clientRequestId: sameRequest ? pending.clientRequestId : crypto.randomUUID(), attachmentIds: uploaded.map(f => f.id), productId: selectedProduct };
      pending = body;
      message('user', text, uploaded.map(f => f.name)); const live = message('assistant', ''); const liveText = live.querySelector('.chat-message-text'); scroll();
      const response = await fetchApi(`/api/product-chat/sessions/${sessionId}/messages`, { method: 'POST', body, stream: true });
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '', completed = false;
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let end;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          const kind = frame.split('\n').find(line => line.startsWith('event: '))?.slice(7);
          const raw = frame.split('\n').filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
          if (!raw) continue; const data = JSON.parse(raw);
          if (epoch !== connectionEpoch) continue;
          if (kind === 'status') { setStatus(data.message); if (data.resetText) liveText.textContent = ''; }
          if (kind === 'delta') { liveText.textContent += data.text; scroll(); }
          if (kind === 'error') { const error = new Error(data.message); error.code = data.code; throw error; }
          if (kind === 'completed') { completed = true; render(data); }
        }
        if (done) break;
      }
      if (!completed) throw new Error('Ответ прервался. Обновите диалог перед повторной отправкой.');
      pending = null; input.value = ''; uploaded = []; selectedProduct = null; context.hidden = true; renderFiles(); setStatus('');
    } catch (error) {
      if (error.code && error.code !== 'session_busy') pending = null;
      try { await reload(); } catch { /* Keep the draft and request ID for an explicit retry. */ }
      setStatus(error.message, true);
    } finally { setBusy(false); renderFiles(); if (dialog.open) input.focus(); }
  });
  input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  attach.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    if (busy) return; const selected = [...fileInput.files]; fileInput.value = '';
    if (selected.length + uploaded.length > 3 || selected.some(f => f.size > 5 * 1024 * 1024)) { setStatus('До 3 файлов, каждый не больше 5 МБ.', true); return; }
    setBusy(true);
    try { await ensureSession(); for (const file of selected) { setStatus(`Загружаю ${file.name}…`); const body = new FormData(); body.append('file', file);
      uploaded.push(await api(`/api/product-chat/sessions/${sessionId}/attachments`, { method: 'POST', body })); } setStatus('Файлы готовы. Добавьте вопрос и отправьте сообщение.'); }
    catch (error) { setStatus(error.message, true); } finally { setBusy(false); renderFiles(); }
  });
  launcher.addEventListener('click', open); close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { launcher.setAttribute('aria-expanded', 'false'); launcher.focus(); });
  refresh.addEventListener('click', async () => { if (busy) return; setBusy(true); setStatus(''); try { if (view === 'list') await showList(); else await reload(); } catch (error) { setStatus(error.message, true); } finally { setBusy(false); } });
  reset.addEventListener('click', () => { if (busy) return; startNew(); input.focus(); });
  all.addEventListener('click', async () => { if (busy) return; setStatus(''); try { await showList(); } catch (error) { setStatus(error.message, true); } focusView(); });
  window.addEventListener('ask-product', async event => { if (busy) return; if (view === 'list') startNew(); selectedProduct = event.detail.id; context.textContent = `Товар: ${event.detail.name}`; context.hidden = false; await open(); input.value = `Расскажите о товаре «${event.detail.name}»`; input.focus(); });
  window.addEventListener('shopping-api-changed', () => { connectionEpoch++; sessionId = null; snapshot = null; clearDraft(); welcome(); setView('list'); if (dialog.open && !busy) open(); });
  setView('list'); welcome();
})();
