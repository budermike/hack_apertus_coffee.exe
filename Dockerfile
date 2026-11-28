# One image with everything: web UI and backend, crawler with embedding into the
# Vector-DB, relevance scoring, summaries and evaluation.
#
#   docker compose up --build        # then open http://localhost:8000
#   docker compose exec app python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-07-01 --to 2026-07-31
#
# The image holds the code, the UI, configs/, prompts/, the Suva examples in
# judgements/, results/, CPU-only PyTorch and the embedding model
# intfloat/multilingual-e5-small (downloaded at build time). data/ (crawled
# judgements, Vector-DB, summaries DB) is mounted as a volume, see docker-compose.yml.
# Outgoing connections at runtime: the Apertus endpoint (LLM_BASE_URL) and the
# court's site when new judgements are fetched.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# All paths in the code (configs/, prompts/, judgements/, data/) are relative to
# the project folder, so the app runs from there.
WORKDIR /app

# Dependencies first: these layers are rebuilt only when requirements.txt changes.
# torch comes from the CPU index first, so pip does not pull the CUDA libraries;
# requirements.txt pins the same version and then finds it installed.
COPY requirements.txt ./
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.15.0" \
    && pip install -r requirements.txt

# Embedding model inside the image, so the first crawl does not wait for a download
# (embed.py looks for it locally first).
ENV HF_HOME=/opt/huggingface \
    EMBEDDING_DEVICE=cpu
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small', device='cpu')"

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
