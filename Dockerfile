FROM python:3.12-slim

WORKDIR /app

# ffmpeg is used to transcode videos in non-browser-safe codecs (most
# commonly HEVC/H.265 from phone recordings) to H.264 before embedding
# them for playback -- see core/video.py:ensure_browser_playable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user rather than the container default (root) -- this
# process only needs to read its own files and the bind-mounted inbox
# folder, so it doesn't need root inside the container.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# Added the browser.serverAddress flag here:
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--browser.serverAddress=localhost"]
