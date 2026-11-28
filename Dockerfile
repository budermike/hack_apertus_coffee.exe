# Backend + web UI in one image (issue #31).
#
#   docker compose up --build        # then open http://localhost:8000
#
# The image holds the code, the UI, configs/, prompts/, the Suva examples in
# judgements/ and results/. data/ (crawled judgements, vector DB, summaries DB) is
# mounted as a volume, see docker-compose.yml. Nothing is downloaded at runtime; the
# only outgoing connection is the Apertus endpoint from LLM_BASE_URL.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# All paths in the code (configs/, prompts/, judgements/, data/) are relative to
# the project folder, so the app runs from there.
WORKDIR /app

# Dependencies first: this layer is rebuilt only when requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps .

COPY configs ./configs
COPY prompts ./prompts
COPY judgements ./judgements
COPY results ./results

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"

# The server stops cleanly on Ctrl-C (KeyboardInterrupt), so `docker stop` sends that
# instead of waiting ten seconds and killing it.
STOPSIGNAL SIGINT

# Options after the entrypoint can be replaced in docker-compose.yml (`command:`).
ENTRYPOINT ["python", "-m", "hack_apertus_coffee_exe.summary.api", "--host", "0.0.0.0", "--port", "8000"]
CMD ["--input", "data/raw", "--static", "src/presentation", "--triage", "results/triage_q2_2026.json"]
