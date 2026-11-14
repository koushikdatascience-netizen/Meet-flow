(function () {
  function normalize(value) {
    return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  }

  function matches(items, query) {
    const text = normalize(query);
    if (!text) return [];
    const words = text.split(/\s+/);
    return items.filter(item => words.every(word => normalize(item.name).includes(word)))
      .sort((a, b) => {
        const score = item => normalize(item.name) === text ? 0 : normalize(item.name).startsWith(text) ? 1 : 2;
        return score(a) - score(b) || a.name.localeCompare(b.name);
      });
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { matches };
  if (typeof document === 'undefined') return;
  const form = document.getElementById('entry-form');
  if (!form) return;
  const rows = document.getElementById('rows');
  const items = Array.from(document.querySelectorAll('#item-options option'), option => ({
    id: option.dataset.id, name: option.value, detail: option.textContent.trim(),
  }));
  let nextId = 0;

  function addRow(initial) {
    const row = document.createElement('div');
    row.innerHTML = '<div class="item-picker"><input class="search" placeholder="Type item name" aria-label="Item search" role="combobox" aria-autocomplete="list" aria-expanded="false" autocomplete="off" required><div class="item-suggestions" role="listbox" aria-label="Matching items" hidden></div><div class="item-search-status" role="status" hidden>No matching items.</div></div><input type="hidden" name="item"><input name="quantity" type="text" inputmode="numeric" pattern="[0-9]+" maxlength="10" placeholder="Pieces" aria-label="Quantity in pieces" title="Whole pieces only, such as 1, 2 or 10" required>';
    rows.append(row);
    const search = row.querySelector('.search');
    const id = row.querySelector('[name=item]');
    const qty = row.querySelector('[name=quantity]');
    const list = row.querySelector('.item-suggestions');
    const status = row.querySelector('.item-search-status');
    list.id = 'item-suggestions-' + (++nextId);
    search.setAttribute('aria-controls', list.id);
    let choices = [], active = -1;

    function close() {
      list.hidden = status.hidden = true;
      search.setAttribute('aria-expanded', 'false');
      search.removeAttribute('aria-activedescendant');
      active = -1;
    }
    function highlight(index) {
      active = index;
      Array.from(list.children).forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
      const option = list.children[active];
      if (option) {
        search.setAttribute('aria-activedescendant', option.id);
        option.scrollIntoView({ block: 'nearest' });
      }
    }
    function select(item) {
      search.value = item.name;
      id.value = item.id;
      search.setCustomValidity('');
      close();
      qty.focus();
    }
    function show() {
      choices = matches(items, search.value).slice(0, 30);
      list.replaceChildren();
      if (!search.value.trim()) { close(); return; }
      choices.forEach((item, index) => {
        const option = document.createElement('div');
        option.id = list.id + '-' + index;
        option.setAttribute('role', 'option');
        const name = document.createElement('strong');
        name.textContent = item.name;
        const detail = document.createElement('small');
        detail.textContent = item.detail;
        option.append(name, detail);
        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => select(item));
        list.append(option);
      });
      list.hidden = !choices.length;
      status.hidden = !!choices.length;
      search.setAttribute('aria-expanded', String(!!choices.length));
      search.removeAttribute('aria-activedescendant');
      active = -1;
      if (choices.length) highlight(0);
    }
    search.addEventListener('input', () => {
      const exact = items.find(item => normalize(item.name) === normalize(search.value));
      id.value = exact ? exact.id : '';
      search.setCustomValidity(exact ? '' : 'Select an item from the suggestions.');
      show();
    });
    search.addEventListener('focus', show);
    search.addEventListener('blur', close);
    search.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (list.hidden) show();
        else if (choices.length) highlight((active + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length);
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (!list.hidden && active >= 0) select(choices[active]);
        else if (id.value) qty.focus();
        else search.reportValidity();
      }
    });
    qty.addEventListener('beforeinput', event => { if (event.data && !/^[0-9]+$/.test(event.data)) event.preventDefault(); });
    qty.addEventListener('input', () => qty.setCustomValidity(/^[0-9]+$/.test(qty.value) && Number(qty.value) > 0 ? '' : 'Enter a positive whole number of pieces.'));
    qty.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); addRow(); } });
    if (initial) { search.value = initial.name; id.value = initial.id; qty.value = initial.quantity; }
    search.focus();
  }

  form.addEventListener('click', event => {
    if (event.target.tagName !== 'BUTTON' || event.target.type !== 'submit') return;
    for (const row of Array.from(rows.children)) {
      if (rows.children.length > 1 && !row.querySelector('.search').value && !row.querySelector('[name=quantity]').value) row.remove();
    }
  });
  document.getElementById('add-row').addEventListener('click', () => addRow());
  const initial = JSON.parse(document.getElementById('initial-rows').textContent);
  if (initial.length) initial.forEach(addRow);
  else addRow();
})();
