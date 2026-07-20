import streamlit as st
import streamlit.components.v1 as components

from core.archive import process_uploads
from core.video import VIDEO_EXTS
from frontend.render import build_gallery_html

st.set_page_config(layout="wide", page_title="Media Viewer", page_icon="\U0001F6E1")

IMAGE_EXTS = ('png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tiff')
UPLOAD_TYPES = ['zip'] + list(IMAGE_EXTS) + [e.lstrip('.') for e in VIDEO_EXTS]


@st.cache_data(show_spinner="Reading files in memory...")
def _process_uploads_cached(file_tuples: tuple):
    return process_uploads(list(file_tuples))


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

if uploaded_files:
    file_tuples = tuple((f.name, f.getvalue()) for f in uploaded_files)
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