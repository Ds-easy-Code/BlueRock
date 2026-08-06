import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.archive import IMAGE_EXTS, process_uploads
from core.queue_state import read_index, write_index
from core.video import VIDEO_EXTS
from frontend.render import build_gallery_html

st.set_page_config(layout="wide", page_title="Media Viewer", page_icon="\U0001F6E1")

# IMAGE_EXTS lives in core.archive now (it's used there for real routing
# decisions) so app.py doesn't keep its own copy that could silently drift
# out of sync -- this list only strips the leading '.' for st.file_uploader.
UPLOAD_TYPES = ['zip'] + [e.lstrip('.') for e in IMAGE_EXTS] + [e.lstrip('.') for e in VIDEO_EXTS]

# Folder the host launcher (host_gui.py) bind-mounts files into.
# See compose.yaml -> volumes: ./inbox:/app/inbox
INBOX_DIR = os.environ.get("BLUEROCK_INPUT_DIR")
QUEUE_DIR = Path(INBOX_DIR) / "queue" if INBOX_DIR else None
STATE_FILE = Path(INBOX_DIR) / "state.json" if INBOX_DIR else None


# max_entries bounds memory: without it, every distinct file processed in a
# long-running session (each queue file, each upload) stays cached forever,
# including its full base64 payload. Only the current item and a little
# slack for back/forward navigation actually need to stay warm.
#
# _progress_cb is prefixed with an underscore on purpose: Streamlit's
# cache_data skips hashing any argument named that way, so passing a fresh
# callback closure (bound to this run's progress bar) on every rerun does
# NOT bust the cache -- a cache hit still returns instantly with no
# progress bar shown, exactly as before this was added.
@st.cache_data(show_spinner=False, max_entries=8)
def _process_uploads_cached(file_tuples: tuple, _progress_cb=None):
    return process_uploads(list(file_tuples), progress_cb=_progress_cb)


def _queue_files():
    """Sorted list of files currently queued by the host launcher. Names only
    are read here; bytes are read lazily, one file at a time, below."""
    if not QUEUE_DIR or not QUEUE_DIR.is_dir():
        return []
    return sorted(
        (f for f in QUEUE_DIR.iterdir() if f.is_file() and not f.name.startswith(".")),
        key=lambda p: p.name,
    )


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
        idx = read_index(STATE_FILE, len(queue))
        st.markdown("---")
        st.markdown(f"**Queue: file {idx + 1} of {len(queue)}**")
        st.caption(queue[idx].name)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("\u2b05 Prev", disabled=idx <= 0, use_container_width=True):
                write_index(STATE_FILE, idx - 1)
                st.rerun()
        with c2:
            if st.button("Next \u27a1", disabled=idx >= len(queue) - 1, use_container_width=True):
                write_index(STATE_FILE, idx + 1)
                st.rerun()

        # Only this ONE file's bytes are read into memory, never the whole queue.
        queue_file_tuple = ((queue[idx].name, queue[idx].read_bytes()),)

uploaded_tuples = tuple((f.name, f.getvalue()) for f in uploaded_files) if uploaded_files else tuple()
file_tuples = queue_file_tuple + uploaded_tuples

if file_tuples:
    progress_ph = st.empty()

    def _on_progress(done: int, total: int) -> None:
        # A fresh closure over progress_ph every rerun -- fine, see the
        # _progress_cb note above on why this doesn't defeat caching.
        frac = min(1.0, done / total) if total else 0.0
        progress_ph.progress(frac, text=f"Processing {min(done, total)} of {total} file(s)...")

    items, errors = _process_uploads_cached(file_tuples, _progress_cb=_on_progress)
    progress_ph.empty()

    for err in errors:
        st.error(err)
    if items:
        final_html = build_gallery_html(items)
        components.html(final_html, height=900, scrolling=False)
    else:
        st.info("No supported images or videos found in what you uploaded.")
else:
    st.info("\U0001F4C1 Upload a ZIP, or drop images/videos directly, from the sidebar to get started.")