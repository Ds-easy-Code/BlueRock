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

// Filenames come from inside a ZIP archive, which anyone could have named
// however they like -- e.g. `<img src=x onerror=alert(1)>.jpg` -- so they
// must never be dropped into innerHTML unescaped.
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
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
    const shortName = escapeHtml(it.name.split('/').pop());
    const fullName = escapeHtml(it.name);
    card.innerHTML = `
      <div class="thumb">
        <img src="${it.thumb}" loading="lazy" alt="">
        <div class="type-pill ${it.type}">${it.type === 'video' ? 'VIDEO' : 'IMAGE'}</div>
        ${it.type === 'video' ? `<div class="play-badge"><div class="circle">${playIconSVG()}</div></div>` : ''}
      </div>
      <div class="meta">
        <div class="name" title="${fullName}">${shortName}</div>
        <div class="sub">${escapeHtml(it.date)} &middot; ${escapeHtml(it.size)}</div>
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
    : it.too_large
    // src was deliberately left empty for large videos (see core/video.py)
    // -- without this branch the lightbox rendered a blank, non-functional
    // <video controls> with no explanation of why nothing played.
    ? `<div class="lb-too-large">
         <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m23 7-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
         <div>This video is too large to preview here (${escapeHtml(it.size)}).</div>
         <div class="lb-too-large-sub">Open it directly from its original folder instead.</div>
       </div>`
    // No `autoplay` here: browsers only allow unmuted autoplay after a
    // user gesture, so autoplaying required `muted`, which meant videos
    // always opened silently. Loading paused instead means the viewer's
    // own click on the ▶ control satisfies that gesture requirement, so
    // it plays with sound like a normal video player.
    : `<video src="${it.src}" controls></video>`;

  // Some videos (very commonly HEVC/H.265, the default recording codec on
  // most modern phones) decode fine for the thumbnail -- OpenCV's backend
  // supports a much wider codec set than browsers do -- but then fail
  // silently in the actual <video> player because the browser itself has
  // no decoder for that codec (Chrome and Firefox generally can't play
  // HEVC at all; Safari usually can). Catch that here rather than leaving
  // a video element that just sits there doing nothing when you hit play.
  const videoEl = lbMediaWrap.querySelector('video');
  if (videoEl) {
    videoEl.addEventListener('error', () => {
      lbMediaWrap.innerHTML = `<div class="lb-too-large">
         <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m23 7-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
         <div>Your browser can't play this video's codec (often HEVC/H.265 from phone recordings).</div>
         <div class="lb-too-large-sub">Try Safari, or open the original file directly in a video player like VLC.</div>
       </div>`;
    });
  }
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

let searchDebounceTimer = null;
searchInput.addEventListener('input', (e) => {
  const value = e.target.value;
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    searchQuery = value;
    applyFilters();
  }, 150);
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