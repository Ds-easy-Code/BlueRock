import json
import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.archive import process_uploads
from core.video import VIDEO_EXTS
from frontend.render import build_gallery_html

st.set_page_config(layout="wide", page_title="Media Viewer", page_icon="\U0001F6E1")

IMAGE_EXTS = ('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tiff')
UPLOAD_TYPES = ['zip'] + list(IMAGE_EXTS) + [e.lstrip('.') for e in VIDEO_EXTS]

# Folder the host launcher (host_gui.py) bind-mounts files into.
# See compose.yaml -> volumes: ./inbox:/app/inbox
INBOX_DIR = os.environ.get("BLUEROCK_INPUT_DIR")
QUEUE_DIR = Path(INBOX_DIR) / "queue" if INBOX_DIR else None
STATE_FILE = Path(INBOX_DIR) / "state.json" if INBOX_DIR else None


@st.cache_data(show_spinner="Reading file...")
def _process_uploads_cached(file_tuples: tuple):
    return process_uploads(list(file_tuples))


def _queue_files():
    """Sorted list of files currently queued by the host launcher. Names only
    are read here; bytes are read lazily, one file at a time, below."""
    if not QUEUE_DIR or not QUEUE_DIR.is_dir():
        return []
    return sorted(
        (f for f in QUEUE_DIR.iterdir() if f.is_file() and not f.name.startswith(".")),
        key=lambda p: p.name,
    )


def _read_index(n: int) -> int:
    if not STATE_FILE or not STATE_FILE.exists():
        return 0
    try:
        idx = json.loads(STATE_FILE.read_text()).get("index", 0)
    except Exception:
        idx = 0
    return max(0, min(idx, max(n - 1, 0)))


def _write_index(idx: int):
    if STATE_FILE:
        STATE_FILE.write_text(json.dumps({"index": idx}))


st.markdown(
    """
    <style>
    header[data-testid="stHeader"] { display: none; }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"], .main { overflow: hidden !important; height: 100vh !important; }
    .block-container { padding: 0 !important; max-width: 100% !important; height: 100vh !important; }
    #MainMenu, footer { display: none; }
    div[data-testid="stAppViewContainer"] > div:first-child { padding-top: 0; }
    section[data-testid="stSidebar"] { width: 260px !important; }
    section[data-testid="stSidebar"] > div { padding-top: 1.2rem; }
    iframe { height: 100vh !important; display: block; }
    div[data-testid="stElementContainer"]:has(iframe) { height: 100vh !important; line-height: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

queue_file_tuple = ()

with st.sidebar:
    st.markdown(
        "<h3 style='margin:0 0 2px 0;font-size:1.1rem'>\U0001F6E1 Media viewer</h3>"
        "<p style='margin:0 0 1rem 0;color:gray;font-size:.78rem;line-height:1.4'>"
        "Decompressed and decoded entirely in the container's memory. Nothing is written to disk for images; "
        "video frames only touch a temp file long enough to grab a thumbnail.</p>",
        unsafe_allow_html=True,
    )
    uploaded_files = st.file_uploader(
        "Upload a ZIP archive, or one or more images/videos directly",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
    )

    queue = _queue_files()
    if queue:
        idx = _read_index(len(queue))
        st.markdown("---")
        st.markdown(f"**Queue: file {idx + 1} of {len(queue)}**")
        st.caption(queue[idx].name)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("\u2b05 Prev", disabled=idx <= 0, use_container_width=True):
                _write_index(idx - 1)
                st.rerun()
        with c2:
            if st.button("Next \u27a1", disabled=idx >= len(queue) - 1, use_container_width=True):
                _write_index(idx + 1)
                st.rerun()

        # Only this ONE file's bytes are read into memory, never the whole queue.
        queue_file_tuple = ((queue[idx].name, queue[idx].read_bytes()),)

uploaded_tuples = tuple((f.name, f.getvalue()) for f in uploaded_files) if uploaded_files else tuple()
file_tuples = queue_file_tuple + uploaded_tuples

if file_tuples:
    items, errors = _process_uploads_cached(file_tuples)
    for err in errors:
        st.error(err)
    if items:
        final_html = build_gallery_html(items)
        components.html(final_html, height=900, scrolling=False)
    else:
        st.info("No supported images or videos found in what you uploaded.")
else:
    st.info("\U0001F4C1 Upload a ZIP, or drop images/videos directly, from the sidebar to get started.")