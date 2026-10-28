(() => {
  'use strict';
  document.querySelectorAll('[data-animation-viewer]').forEach(select => {
    const viewer = document.getElementById(select.dataset.animationViewer);
    if (!viewer) return;
    const populate = () => {
      select.replaceChildren();
      (viewer.availableAnimations || []).forEach(name => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = ({Idle_3: 'Ожидание', walking_2: 'Ходьба', run_fast_3: 'Бег'})[name] || name;
        select.append(option);
      });
      select.disabled = !select.options.length;
    };
    viewer.addEventListener('load', populate);
    if (viewer.loaded) populate();
    select.addEventListener('change', () => { viewer.animationName = select.value; viewer.play(); });
  });
  document.querySelector('[data-refresh]')?.addEventListener('click', () => location.reload());
  let changedForm = false;
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('input', () => { changedForm = true; });
    form.addEventListener('submit', event => {
      if (form.dataset.sent) { event.preventDefault(); return; }
      form.dataset.sent = '1';
      // Leave submitter enabled so its decision name/value reaches Django.
      form.setAttribute('aria-busy', 'true');
      setTimeout(() => { delete form.dataset.sent; form.removeAttribute('aria-busy'); }, 15000);
    });
  });
  const source = document.querySelector('#id_source');
  const syncSource = () => {
    const isText = source.value === 'text';
    document.querySelector('#photo-fields').hidden = isText;
    const photoInput = document.querySelector('#id_image');
    photoInput.disabled = isText;
    photoInput.required = !isText;
    document.querySelector('#id_text_prompt').required = isText;
  };
  if (source) { source.addEventListener('change', syncSource); syncSource(); }
  const input = document.querySelector('#id_image');
  const zone = document.querySelector('#upload-zone');
  if (input && zone) {
    let previousUrl;
    const preview = () => {
      const file = input.files?.[0];
      if (!file) return;
      const label = document.querySelector('#upload-label');
      label.textContent = file.name;
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
        label.textContent = 'Нужен JPG, PNG или WebP до 10 МБ';
        input.setCustomValidity(label.textContent);
        return;
      }
      input.setCustomValidity('');
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      previousUrl = URL.createObjectURL(file);
      const img = document.querySelector('#upload-preview');
      img.src = previousUrl; img.hidden = false;
      zone.classList.add('has-image');
    };
    input.addEventListener('change', preview);
    ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, e => {e.preventDefault();zone.classList.add('dragging');}));
    ['dragleave', 'drop'].forEach(name => zone.addEventListener(name, e => {e.preventDefault();zone.classList.remove('dragging');}));
    zone.addEventListener('drop', e => {
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; changedForm = true; preview(); }
    });
  }
  if (document.body.dataset.auth !== '1') return;
  let revision;
  const poll = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch('/updates/', {headers: {'Accept':'application/json'}, cache:'no-store'});
      if (!response.ok || response.redirected) return;
      const data = await response.json();
      if (revision && data.revision !== revision) {
        document.querySelector('#live-update').hidden = false;
        document.title = '● Обновление проекта · FORMA';
        if (document.body.dataset.auto === '1' && !changedForm && !document.activeElement?.matches('input,textarea,select')) {
          location.reload();
        }
      }
      revision = data.revision;
    } catch (_) { /* Resume polling when connection returns. */ }
  };
  poll(); setInterval(poll, 8000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
