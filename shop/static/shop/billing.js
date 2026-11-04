(() => {
  const products = new Map(JSON.parse(document.getElementById('products-data').textContent).map(p => [String(p.id), p]));
  const form = document.getElementById('checkout');
  const cart = document.getElementById('cart');
  const management = document.getElementById('id_items-TOTAL_FORMS');
  const config = document.getElementById('tax-config').dataset;
  const purchase = config.kind === 'purchase';
  const search = document.getElementById('product-search');
  const catalog = document.getElementById('catalog');
  const status = document.getElementById('search-status');
  const buttons = new Map([...catalog.querySelectorAll('.product')].map(button => [button.dataset.id, button]));
  const currency = n => '₹' + n.toFixed(2);
  const round = n => Math.round((n + Number.EPSILON) * 100) / 100;
  const activeRows = () => [...cart.querySelectorAll('.line')].filter(row => !row.querySelector('[name$="-DELETE"]').checked);
  let matches = [], selected = 0;
  function focusSearch() {
    search.value = ''; renderMatches(); search.focus();
    if (matches.length) status.textContent = 'Type the next product name, then press Enter to add it.';
  }
  function unavailable(product) {
    status.textContent = `${product.name} has no stock. Add a purchase before selling it.`;
    status.classList.add('stock-error');
    search.focus();
  }
  function renderMatches() {
    status.classList.remove('stock-error');
    matches = MeatFlowSearch.rank([...products.values()], search.value, purchase);
    const visible = new Set(matches.map(product => String(product.id)));
    for (const [id, button] of buttons) button.hidden = !visible.has(id);
    for (const product of matches) catalog.append(buttons.get(String(product.id)));
    matches = matches.filter(product => purchase || Number(product.stock) > 0);
    selected = 0;
    catalog.scrollTop = 0;
    highlight();
    document.getElementById('no-matches').hidden = visible.size !== 0 || !products.size;
    if (visible.size && !matches.length) { status.textContent = 'Matching items are out of stock.'; return; }
    status.textContent = search.value.trim() ? `${visible.size} match${visible.size === 1 ? '' : 'es'} · Enter to select · ↑ ↓ to choose` : 'Enter selects the first item · ↑ ↓ to choose';
  }
  function highlight() {
    for (const button of buttons.values()) { button.classList.remove('highlighted'); button.setAttribute('aria-selected', 'false'); }
    const product = matches[selected];
    if (product) {
      const button = buttons.get(String(product.id));
      button.classList.add('highlighted'); button.setAttribute('aria-selected', 'true');
      search.setAttribute('aria-activedescendant', button.id);
    } else search.removeAttribute('aria-activedescendant');
  }
  function totals() {
    let subtotal = 0, count = 0;
    const rows = activeRows().map(row => {
      const product = products.get(row.querySelector('select').value);
      const quantityInput = row.querySelector('[name$="-quantity"]');
      const quantity = Number(quantityInput.value) || 0;
      const cost = row.querySelector('[name$="-unit_cost"]');
      const amount = product ? round(quantity * Number(purchase ? cost.value : product.price)) : 0;
      row.querySelector('.rate').textContent = currency(amount);
      row.classList.toggle('chosen', Boolean(product));
      row.hidden = !product && !row.querySelector('.errorlist');
      row.querySelector('.chosen-name').textContent = product ? product.name : '';
      if (product) {
        quantityInput.step = product.unit === 'pc' ? '1' : '0.001';
        row.querySelector('.quantity-field label').textContent = `Qty (${product.unit})`;
        count++;
      }
      subtotal += amount;
      return {product, amount};
    });
    const discount = Math.max(0, Number(document.getElementById('id_discount')?.value) || 0);
    const state = document.getElementById('id_customer_state')?.value || document.getElementById('id_customer_gstin')?.value.trim().match(/^[0-9]{2}/)?.[0];
    const interstate = state && state !== config.state;
    let tax = 0, cumulative = 0, allocated = 0;
    for (const row of rows) {
      cumulative += row.amount;
      const target = subtotal ? round(discount * cumulative / subtotal) : 0;
      const base = Math.max(0, row.amount - (target - allocated)); allocated = target;
      if (config.mode === 'tax' && row.product) tax += interstate ? round(base * Number(row.product.tax_rate) / 100) : 2 * round(base * Number(row.product.tax_rate) / 200);
    }
    document.querySelector('.summary-breakdown').hidden = purchase || (discount === 0 && tax === 0);
    document.getElementById('subtotal').textContent = currency(subtotal);
    document.getElementById('discount').textContent = currency(discount);
    document.getElementById('tax').textContent = currency(tax);
    document.getElementById('total').textContent = currency(Math.max(0, subtotal - discount + tax));
    document.getElementById('empty-cart').hidden = count > 0;
    document.getElementById('item-count').textContent = `${count} item${count === 1 ? '' : 's'}`;
  }
  function newRow() {
    const index = Number(management.value);
    if (index >= 100) { status.textContent = 'A bill can contain at most 100 rows.'; return null; }
    const holder = document.createElement('div');
    holder.innerHTML = document.getElementById('empty-row').innerHTML.replaceAll('__prefix__', String(index));
    const row = holder.firstElementChild;
    cart.append(row); management.value = index + 1;
    return row;
  }
  function selectProduct(id) {
    const product = products.get(String(id));
    if (!product) return;
    if (!purchase && Number(product.stock) <= 0) { unavailable(product); return; }
    const existing = activeRows().find(row => row.querySelector('select').value === String(id));
    const row = existing || activeRows().find(row => !row.querySelector('select').value) || newRow();
    if (!row) return;
    row.hidden = false;
    row.querySelector('select').value = String(id);
    const input = row.querySelector('[name$="-quantity"]');
    if (!existing) {
      input.value = '1';
      if (purchase) row.querySelector('[name$="-unit_cost"]').value = product.price;
    }
    search.value = ''; renderMatches(); totals(); input.focus(); input.select();
    if (existing) status.textContent = 'Already added — change its quantity.';
  }
  for (const [id,button] of buttons) {
    if (!purchase && Number(products.get(id).stock) <= 0) {
      button.setAttribute('aria-disabled','true');
      button.classList.add('out-of-stock');
      button.querySelector('.muted').textContent = 'Out of stock — add a purchase first';
    }
  }
  document.getElementById('add-row').addEventListener('click', focusSearch);
  catalog.addEventListener('click', e => { const button = e.target.closest('.product'); if (button) selectProduct(button.dataset.id); });
  search.addEventListener('input', renderMatches);
  search.addEventListener('keydown', e => {
    if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (!matches.length) return;
      selected = (selected + (e.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
      highlight(); buttons.get(String(matches[selected].id)).scrollIntoView({block:'nearest'});
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (matches[selected]) selectProduct(matches[selected].id);
      else if (products.size) { status.textContent = 'No available matching product. Check its stock or add a purchase.'; status.classList.add('stock-error'); }
    } else if (e.key === 'Escape') { e.preventDefault(); focusSearch(); }
  });
  cart.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.ctrlKey || e.metaKey || e.isComposing) return;
    if (e.target.matches('[name$="-quantity"], [name$="-unit_cost"]')) {
      e.preventDefault();
      focusSearch();
    }
  });
  cart.addEventListener('click', e => {
    if (!e.target.closest('.remove')) return;
    const row = e.target.closest('.line'); row.querySelector('[name$="-DELETE"]').checked = true; row.hidden = true; totals(); focusSearch();
  });
  form.addEventListener('input', totals);
  form.addEventListener('change', e => {
    if (!purchase && e.target.matches('select[name$="-product"]')) {
      const product = products.get(e.target.value);
      if (product && Number(product.stock) <= 0) { e.target.value = ''; totals(); unavailable(product); return; }
    }
    if (purchase && e.target.matches('select[name$="-product"]')) {
      const product = products.get(e.target.value);
      if (product) e.target.closest('.line').querySelector('[name$="-unit_cost"]').value = product.price;
    }
    totals();
  });
  form.querySelectorAll('button[type="submit"]').forEach(button => button.dataset.label = button.textContent);
  window.addEventListener('pageshow', () => {
    delete form.dataset.saving;
    form.querySelectorAll('button[type="submit"]').forEach(button => { button.textContent = button.dataset.label; button.removeAttribute('aria-disabled'); });
  });
  form.addEventListener('submit', e => {
    for (const row of activeRows()) if (!row.querySelector('select').value) row.querySelector('[name$="-DELETE"]').checked = true;
    // Keep the clicked submitter enabled: its action must be included in the POST.
    if (form.dataset.saving) { e.preventDefault(); return; }
    form.dataset.saving = 'true';
    const button = e.submitter || form.querySelector('[data-primary]'); button.textContent = 'Saving…';
    form.querySelectorAll('button[type="submit"]').forEach(item => item.setAttribute('aria-disabled','true'));
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'F2') { e.preventDefault(); focusSearch(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); form.requestSubmit(form.querySelector('[data-primary]')); }
  });
  renderMatches(); totals();
  if (!form.querySelector('.errorlist')) search.focus();
})();
