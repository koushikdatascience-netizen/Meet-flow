(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.querySelector('.mobile-toggle');
  const backdrop = document.querySelector('.nav-backdrop');
  const closeButton = document.querySelector('.nav-close');
  if (!sidebar || !toggle || !backdrop) return;
  const mobile = window.matchMedia('(max-width: 680px)');
  function setOpen(open, restoreFocus = false) {
    open = mobile.matches && open;
    document.body.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    backdrop.hidden = !open;
    sidebar.inert = mobile.matches && !open;
    if (mobile.matches) sidebar.setAttribute('aria-hidden', String(!open));
    else sidebar.removeAttribute('aria-hidden');
    if (open) closeButton.focus();
    else if (restoreFocus) toggle.focus();
  }
  toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('nav-open')));
  closeButton.addEventListener('click', () => setOpen(false, true));
  backdrop.addEventListener('click', () => setOpen(false, true));
  sidebar.addEventListener('click', event => { if (event.target.closest('a')) setOpen(false); });
  document.addEventListener('keydown', event => {
    if (!mobile.matches || !document.body.classList.contains('nav-open')) return;
    if (event.key === 'Escape') { setOpen(false, true); return; }
    if (event.key !== 'Tab') return;
    const controls = Array.from(sidebar.querySelectorAll('a[href], button, input:not([type="hidden"]), select, [tabindex="0"]')).filter(el => !el.disabled && el.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  mobile.addEventListener('change', () => setOpen(false));
  setOpen(false);
})();
