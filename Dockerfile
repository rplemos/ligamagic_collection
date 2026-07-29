# Playwright's own image: Chromium and every system library it needs are
# already installed, which is the fiddly part of hosting this anywhere.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# This MUST match the image tag above. The image ships Chromium builds for one
# specific Playwright version, and a mismatched pip package looks for a browser
# revision directory that isn't there.
ARG PLAYWRIGHT_VERSION=1.47.0

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Run as a non-root user. The image may already have a user at UID 1000, so
# only create one if that ID is free, and keep the browsers readable for it.
RUN (id -u 1000 >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash user) \
    && chmod -R a+rX /ms-playwright

# Install playwright system-wide rather than relying on the image's own copy:
# that one lives in root's user site-packages, so a non-root process can't
# import it.
RUN pip install --no-cache-dir \
        "playwright==${PLAYWRIGHT_VERSION}" \
        "flask>=3.0" \
        "gunicorn>=21.2"

WORKDIR /app
COPY --chown=1000:0 collection.py app.py ./
COPY --chown=1000:0 templates ./templates

USER 1000
# Chromium needs a writable home for its profile and cache.
ENV HOME=/tmp

# Prove, as the runtime user, that playwright imports and that the browser it
# wants actually exists in the image. If this is wrong the build fails here
# with a clear message, instead of the container crash-looping on deploy.
RUN python3 -c "\
import os, sys;\
from playwright.sync_api import sync_playwright;\
p = sync_playwright().start();\
path = p.chromium.executable_path;\
print('chromium:', path);\
sys.exit('MISSING: ' + path) if not os.path.exists(path) else None;\
p.stop();\
print('playwright OK')"

# Hosts differ on which port they expect: Render and Cloud Run inject $PORT,
# so honour it and fall back to 7860.
ENV PORT=7860
EXPOSE 7860

# One worker, threaded: a scrape holds a connection open for minutes, and the
# generous timeout keeps gunicorn from killing it mid-run. Shell form so $PORT
# is expanded at runtime.
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 900 app:app
