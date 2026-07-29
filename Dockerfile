# Playwright's own image: Chromium and every system library it needs are
# already installed, which is the fiddly part of hosting this anywhere.
# The tag must stay in sync with the bundled Playwright Python package, so we
# deliberately do NOT pip-install playwright here — it's already the right version.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Hugging Face Spaces runs containers as UID 1000. The Playwright image may
# already have a user at that ID, so only create one if it's free, and make
# sure the pre-installed browsers stay readable for it.
RUN (id -u 1000 >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash user) \
    && chmod -R a+rX /ms-playwright

RUN pip install --no-cache-dir "flask>=3.0" "gunicorn>=21.2"

WORKDIR /app
COPY --chown=1000:0 collection.py app.py ./
COPY --chown=1000:0 templates ./templates

USER 1000
# Chromium needs a writable home for its profile and cache.
ENV HOME=/tmp

# Hosts differ on which port they expect: Cloud Run injects $PORT (8080),
# Render injects $PORT (10000), HF Spaces wants 7860. Honour whatever is set.
ENV PORT=7860
EXPOSE 7860

# One worker, threaded: a scrape holds a connection open for minutes, and the
# generous timeout keeps gunicorn from killing it mid-run. Shell form so $PORT
# is expanded at runtime.
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 900 app:app
