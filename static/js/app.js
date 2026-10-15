(() => {
  const loader = document.querySelector('.modal-loader');
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link || !loader || event.defaultPrevented || event.metaKey || event.ctrlKey || link.target || link.origin !== location.origin || link.getAttribute('href').startsWith('#')) return;
    loader.classList.add('show');
  });
  window.addEventListener('pageshow', () => loader?.classList.remove('show'));
  document.querySelectorAll('[data-menu-toggle]').forEach((button) => button.addEventListener('click', () => document.querySelector('.sidebar')?.classList.toggle('open')));
  document.querySelectorAll('[data-dismiss]').forEach((item) => setTimeout(() => item.remove(), 6500));
})();
