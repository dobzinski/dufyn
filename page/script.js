document.getElementById('year').textContent=new Date().getFullYear();

(() => {
  const orbit = document.getElementById('discovery-orbit');
  if (!orbit) return;
  const nodes = [...orbit.querySelectorAll('.node')];
  const links = [...orbit.querySelectorAll('[data-link]')];
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let raf = 0;
  function render(time) {
    const t = reduceMotion.matches ? 0 : time * 0.00017;
    nodes.forEach((node, i) => {
      const angle = t + i * Math.PI * 2 / nodes.length - Math.PI / 2;
      const x = 237.5 + Math.cos(angle) * 184;
      const y = 237.5 + Math.sin(angle) * 176;
      node.style.left = (x / 475 * 100) + '%';
      node.style.top = (y / 475 * 100) + '%';
      links[i].setAttribute('d', `M237.5 237.5 L${x.toFixed(2)} ${y.toFixed(2)}`);
    });
    if (!reduceMotion.matches) raf = requestAnimationFrame(render);
  }
  function refresh() { cancelAnimationFrame(raf); render(0); }
  reduceMotion.addEventListener?.('change', refresh);
  if (reduceMotion.matches) render(0);
  else raf = requestAnimationFrame(render);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelAnimationFrame(raf);
    else refresh();
  });
})();

(() => {
  const viewport = document.getElementById('dashboard-preview');
  const image = document.getElementById('dashboard-preview-image');
  const buttons = [...document.querySelectorAll('[data-preview-view]')];
  buttons.forEach(button => button.addEventListener('click', () => {
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    // Section offsets are measured in the screenshot's original pixel coordinates.
    const sourceWidth = image.naturalWidth || Number(image.getAttribute('width'));
    const position = Number(button.dataset.previewY || 0) * image.clientWidth / sourceWidth;
    viewport.scrollTo({top:position, behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }));
})();
