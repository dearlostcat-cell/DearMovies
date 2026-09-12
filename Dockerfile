FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements-browser.txt ./
ARG INSTALL_BROWSER=false
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_BROWSER" = "true" ]; then pip install --no-cache-dir -r requirements-browser.txt && python -m playwright install --with-deps chromium; fi
RUN useradd --create-home --uid 10001 bot
COPY --chown=bot:bot bot ./bot
COPY --chown=bot:bot config ./config
COPY --chown=bot:bot assets ./assets
COPY --chown=bot:bot main.py status.html ./
RUN mkdir -p data assets && chown -R bot:bot /app
USER bot
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).read()"
CMD ["python", "main.py"]
