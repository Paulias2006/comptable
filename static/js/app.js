(() => {
  const loader = document.querySelector('.modal-loader');
  const isDownload = (element) => {
    const href = element?.getAttribute?.('href') || '';
    return element?.hasAttribute?.('data-download') || /\/telecharger\//.test(href) || /\/exports\/\?type=/.test(href) || /\/sauvegarde\//.test(href);
  };
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href]');
    if (!link || !loader || event.defaultPrevented || event.metaKey || event.ctrlKey || link.target || link.origin !== location.origin || link.getAttribute('href').startsWith('#') || isDownload(link)) return;
    loader.classList.add('show');
  });
  document.addEventListener('submit', (event) => {
    if (event.defaultPrevented) return;
    const form = event.target;
    if (form.hasAttribute('data-download')) return;
    const button = event.submitter;
    if (button) {
      if (button.disabled) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.insertAdjacentHTML('afterbegin', '<span class="button-spinner" aria-hidden="true"></span>');
    } else {
      loader?.classList.add('show');
    }
  });
  window.addEventListener('pageshow', () => {
    loader?.classList.remove('show');
    document.querySelectorAll('[aria-busy="true"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.querySelector('.button-spinner')?.remove();
    });
  });
  document.querySelectorAll('[data-menu-toggle]').forEach((button) => button.addEventListener('click', () => document.querySelector('.sidebar')?.classList.toggle('open')));
  document.querySelectorAll('[data-dismiss]').forEach((item) => setTimeout(() => item.remove(), 6500));
})();
