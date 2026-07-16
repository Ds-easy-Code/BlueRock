let searchQuery = '';
let typeFilter = 'all'; // 'all' | 'image' | 'video'
let filtered = ITEMS.map((it, i) => i);
let currentIndex = null;

const grid = document.getElementById('grid');
const emptyState = document.getElementById('emptyState');
const visibleCount = document.getElementById('visibleCount');
const lightbox = document.getElementById('lightbox');
const lbMediaWrap = document.getElementById('lbMediaWrap');
const lbName = document.getElementById('lbName');
const lbSub = document.getElementById('lbSub');
const lbPrev = document.getElementById('lbPrev');
const lbNext = document.getElementById('lbNext');
const filterGroup = document.getElementById('filterGroup');
const searchInput = document.getElementById('searchInput');

function playIconSVG() {
  return '<svg width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>';
}

function updateCounts() {
  const imageCount = ITEMS.filter(it => it.type === 'image').length;
  const videoCount = ITEMS.filter(it => it.type === 'video').length;
  document.getElementById('countAll').textContent = ITEMS.length;
  document.getElementById('countImage').textContent = imageCount;
  document.getElementById('countVideo').textContent = videoCount;
}

function applyFilters() {
  const q = searchQuery.trim().toLowerCase();
  filtered = ITEMS
    .map((it, i) => i)
    .filter(i => {
      const it = ITEMS[i];
      const matchesType = typeFilter === 'all' || it.type === typeFilter;
      const matchesQuery = !q || it.name.toLowerCase().includes(q);
      return matchesType && matchesQuery;
    });

  visibleCount.textContent = (q || typeFilter !== 'all')
    ? `${filtered.length} / ${ITEMS.length} files`
    : `${ITEMS.length} files`;

  renderGrid();
}

function renderGrid() {
  grid.innerHTML = '';
  emptyState.style.display = filtered.length === 0 ? 'flex' : 'none';
  filtered.forEach(origIdx => {
    const it = ITEMS[origIdx];
    const card = document.createElement('div');
    card.className = 'card';
    card.onclick = () => openLightbox(origIdx);
    const shortName = it.name.split('/').pop();
    card.innerHTML = `
      <div class="thumb">
        <img src="${it.thumb}" loading="lazy" alt="">
        <div class="type-pill ${it.type}">${it.type === 'video' ? 'VIDEO' : 'IMAGE'}</div>
        ${it.type === 'video' ? `<div class="play-badge"><div class="circle">${playIconSVG()}</div></div>` : ''}
      </div>
      <div class="meta">
        <div class="name" title="${it.name}">${shortName}</div>
        <div class="sub">${it.date} &middot; ${it.size}</div>
      </div>
    `;
    grid.appendChild(card);
  });
}

function openLightbox(origIdx) {
  currentIndex = filtered.indexOf(origIdx);
  renderLightbox();
  lightbox.classList.add('open');
}

function renderLightbox() {
  const origIdx = filtered[currentIndex];
  const it = ITEMS[origIdx];
  const shortName = it.name.split('/').pop();
  lbMediaWrap.innerHTML = it.type === 'image'
    ? `<img src="${it.src}" alt="">`
    : `<video src="${it.src}" controls autoplay></video>`;
  lbName.textContent = shortName;
  lbSub.textContent = `${currentIndex + 1} / ${filtered.length}  \u00b7  ${it.date}  \u00b7  ${it.size}`;
  lbPrev.classList.toggle('disabled', currentIndex === 0);
  lbNext.classList.toggle('disabled', currentIndex === filtered.length - 1);
}

function closeLightbox() {
  lightbox.classList.remove('open');
  const v = lbMediaWrap.querySelector('video');
  if (v) v.pause();
}

function step(delta) {
  const newIndex = currentIndex + delta;
  if (newIndex < 0 || newIndex >= filtered.length) return;
  currentIndex = newIndex;
  renderLightbox();
}

lbPrev.onclick = (e) => { e.stopPropagation(); step(-1); };
lbNext.onclick = (e) => { e.stopPropagation(); step(1); };
document.getElementById('lbClose').onclick = closeLightbox;
lightbox.addEventListener('click', (e) => { if (e.target === lightbox) closeLightbox(); });

document.addEventListener('keydown', (e) => {
  if (!lightbox.classList.contains('open')) return;
  if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'Escape') closeLightbox();
});

let touchStartX = null;
lightbox.addEventListener('touchstart', (e) => { touchStartX = e.changedTouches[0].clientX; });
lightbox.addEventListener('touchend', (e) => {
  if (touchStartX === null) return;
  const dx = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(dx) > 50) step(dx > 0 ? -1 : 1);
  touchStartX = null;
});

searchInput.addEventListener('input', (e) => {
  searchQuery = e.target.value;
  applyFilters();
});

filterGroup.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    filterGroup.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    typeFilter = btn.dataset.filter;
    applyFilters();
  });
});

updateCounts();
applyFilters();