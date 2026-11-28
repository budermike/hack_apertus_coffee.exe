# Hack Apertus Zurich — Challenge Partner: Suva

## Our solution: Urteilsradar (Team Coffee.exe)

The Federal Supreme Court publishes about 2000 judgements per quarter; Suva's legal specialists need the roughly 30 that matter for accident insurance, summarised in German and French. **Urteilsradar** fetches the judgements, lets **Apertus v1.5** score every candidate in two passes and explain the score with quotes from the judgement, writes the summaries in Suva's style in both languages, and shows everything in a web interface where lawyers search, read, correct and decide.

- Measured on the two quarters with a known Suva selection, always with the calibration of the other quarter: 49 of 59 selected judgements found at the F2-optimal threshold, 58 of 59 at the shipped setting (a recall target of 95 %, longer list). Why each threshold sits where it does is shown next to the score in the UI. Details and limits: [`technical_report.md`](technical_report.md).
- How it is built: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Every model used: [`docs/MODELS.md`](docs/MODELS.md). Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md).
- Demo video and pitch deck: linked in `technical_report.md` and the submission form.

How to install, run and use it: [Getting started](#getting-started).

## 🎯 The Challenge

### Identification of relevant Federal Court judgements and preparation of a legal summary

Coffee.exe
<br>
<br>

Template repository for [Hack Apertus](https://hackapertus.ch/) submissions.
Every project keeps the same layout, so organizers and judges find the same
things in the same place.

Submissions must use the v1.5 Apertus model family.
The submission must comply with the [HackApertus T&C](https://hackapertus.ch/terms-and-conditions) and the [model and challenge requirements](https://hackapertus.notion.site/zurich-challenges-model-requirements)


## 🔧 Resources & Tools

Check our resources & tools page for more information:
[Resources & Tools](https://hackapertus.notion.site/zurich-resources-tools)

| Models           | URL                                      |
|------------------|------------------------------------------|
| Apertus v1.5 8B  | [huggingface.co/swiss-ai/Apertus-v1.5-8B](https://huggingface.co/swiss-ai/Apertus-v1.5-8B)  |
| Apertus v1.5 70B | [huggingface.co/swiss-ai/Apertus-v1.5-70B](https://huggingface.co/swiss-ai/Apertus-v1.5-70B) |

## The structure

Keep the files and directories as shown below — don't rename or move them.

| Path | What it is |
| --- | --- |
| `README.md` | The challenge description and submission requirements |
| `technical_report.md` | The deeper write-up: architecture, evaluation, limitations |
| `challenge_description.md` | Complete Challenge description incl. deliverables 
| `LICENSE` | Apache-2.0 |
| `src/` | Your code |
| `src/hack_apertus_coffee_exe/` | The Python package: `crawl/`, `triage/`, `summary/`, `present/`, `eval/` |
| `src/presentation/` | The web interface (plain JavaScript, served by the backend) |
| `src/keera_example.py` | One test call to the Apertus gateway (`LLM_*` variables) |
| `configs/` | Settings: paths, crawl source, thresholds |
| `prompts/` | Prompts of the summaries: core question, writing (DE/FR), style profiles, glossary; earlier versions in `prompts/v1/`, `v2/`, `v4/` |
| `notebooks/` | Data analyses behind thresholds, model and prompt choices (`triage/`, `summary/`) |
| `judgements/` | Suva's example summaries and selection lists (reference data of the challenge partner) |
| `data/` | `raw/`: fetched judgements; `processed/`: Vector-DB, caches, summaries database |
| `results/` | Scores, lists, evaluation tables and reports |
| `tests/` | Unit tests (no network) |
| `docs/` | Architecture, model register, decisions, schema, audit |
| `Dockerfile`, `docker-compose.yml` | One container with everything: web interface, backend, crawler with embeddings, scoring, summaries |

## Environment

- Include instructions for running the project in your technical report.
- Use the following environment variables:
```
LLM_NAME — name and version of the model
LLM_BASE_URL — endpoint base URL
LLM_API_KEY — your API key
```

## 📦 Submission Requirements & Deliverables

The submission must:
1. follow the template repo and include all prerequisite files and definitions
2. run end-to-end when judges try to run it

### Git repo (URL)
- Create your repo from this template (**Use this template**).
- Keep the layout as it is: don't rename or move its files.
- Set the repo to PUBLIC (Settings --> Collaborators --> Manage Visibility)
- Submit the URL of YOUR Git repo (keep it to 'public').

### Technical Report (pdf)
- Update [technical_report.md](technical_report.md) in this repository with all the details for your submission.
- Upload a pdf of your technical report to the root of the repo, named as `TeamName_Report.pdf`.
- Format: pdf, max. 6 pages w/o references
- Submit the pdf of the technical report.

### Demo video (URL)
- Max. 2 min demo video of your prototype
- Add the link to the **Demo** field at the top of [technical_report.md](technical_report.md)
- Submit the URL of the video.

### Pitch deck (pdf)
- Max. 10 slides

## Getting started

### 1. Requirements

- Python 3.13, about 2 GB of disk space, no GPU.
- Access to an Apertus v1.5 endpoint (CSCS/Keera gateway or any OpenAI-compatible server with Apertus). Without a key the web application still starts and shows the shipped results; only new Apertus calls need it.

### 2. Install and configure

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime (incl. PyTorch and the embedding library) + tests and notebooks
cp .env.example .env                       # then fill in LLM_API_KEY
```

| Variable | Purpose | Default in `.env.example` |
| --- | --- | --- |
| `LLM_API_KEY` | key for the Apertus gateway (secret, only in `.env`) | – |
| `LLM_BASE_URL` | Apertus endpoint | `https://gateway.keera.ch/api/v1` |
| `LLM_NAME` | model for the gateway test call and the older triage | `apertus-v1-5-8b` |
| `SUMMARY_MODEL` | model for the German and French summaries | `apertus-v1-5-70b` |
| `SUMMARY_DB_PATH` | SQLite file of the knowledge base (summaries, edits, decisions) | `data/processed/summaries.db` |
| `EMBEDDING_MODEL` | open-weights embedding model; `crawl.ensure` embeds newly fetched judgements with it into the Vector-DB | `intfloat/multilingual-e5-small` |

The relevance scoring uses `apertus-v1-5-70b` unless `--model` says otherwise. The older names `APERTUS_*` still work as aliases.

### 3. Check the installation

```
pytest                                     # unit tests, no network, under 15 seconds
python src/keera_example.py                # one test call to the Apertus gateway
```

### 4. Start the web application

On the shipped results for Q2/2026 (1 April to 30 June 2026, the last complete quarter):

```
python -m hack_apertus_coffee_exe.summary.api --static src/presentation --triage results/triage_q2_2026.json
# open http://127.0.0.1:8000
```

or in a container (same application, see [Deployment](#deployment)):

```
docker compose up --build                  # open http://localhost:8000
```

Useful options of `summary.api`: `--auto-summarize` generates the summaries of all judgements on the list right at startup (Apertus calls), `--port` / `--host` change the address, `--model` overrides `SUMMARY_MODEL`.

### 5. Use it

1. **Search a period.** Enter 01.04.2026 to 30.06.2026 and search. The list shows every judgement of the period with its relevance score; sort by score, date or case number, filter by text. Judgements on the Suva list are marked as relevant.
2. **Read why.** Open a judgement: the reason for the score (also when it is *not* relevant), the criteria with verbatim quotes from the judgement and whether each quote was found exactly, and the original judgement, shown from the stored text without leaving the installation, with a link to the source.
3. **Summary in German and French.** After a search the summaries of the relevant judgements are queued on the server and generated one after the other; any other judgement can be summarised on request. Generated texts are stored in the knowledge base and never generated twice.
4. **Correct and release.** Edit a summary in one language, let Apertus bring the other language in line, and release the edited version. The knowledge base keeps the generated text and every edit.
5. **Decide.** «Für Suva relevant» / «Nicht relevant» stores the lawyer's decision; similar judgements move with it, and only the lawyer's own decision takes a judgement off the list.
6. **Knowledge base.** Search all stored summaries in full text.
7. **Other periods.** For a period without results the server fetches the judgements of the social-law divisions and scores them in the background (internet and Apertus needed; no embedding, so this also works in the container). A single judgement can be added by its case number through the API (`POST /api/judgements/add`); the interface has no button for it yet.

### 6. Run the pipeline yourself

For a new quarter, from fetching to summaries (several hours of Apertus calls on the shared gateway):

```
python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-04-01 --to 2026-06-30
python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2 --output results/triage_q2_2026.json
python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage_q2_2026.json
```

- `crawl.ensure` fetches only the days that are missing and embeds them into the Vector-DB with the embedding model; `--source bger` reads bger.ch instead of the mirror entscheidsuche.ch (`configs/crawl.json`).
- `triage.cli relevance` scores the period in two Apertus passes and calibrates on the labelled quarters (`configs/triage.json`). `--model apertus-v1-5-70b-thinking` switches on the reasoning mode, `--target-recall` sets the recall target of the list, `--from-scores` recomputes list and exports from stored scores without any Apertus call.
- `summary.run batch` summarises every judgement on the list; `summary.run one --case 8C_123/2025` summarises a single one, `summary.run show --case …` prints what is stored.

### 7. Reproduce the evaluation

All numbers in `technical_report.md` come from stored answers in `results/`, without a model call:

```
python -m hack_apertus_coffee_exe.eval.relevance_eval      # results/relevance_eval.md
python -m hack_apertus_coffee_exe.eval.feedback_sim        # results/reports/feedback_learning.md
```

The analyses behind thresholds, models and prompts are in `notebooks/triage/` (relevance, model variants, pitch charts in `results/figures/`) and `notebooks/summary/` (style of Suva's summaries, 4 models × 4 prompt versions). They read the stored results and call no model.

## Deployment

Everything runs in one container (`Dockerfile`, `docker-compose.yml`): web UI and backend, the crawler with embedding into the Vector-DB, relevance scoring, summaries and evaluation. The image holds the code, the UI, `configs/`, `prompts/`, the Suva examples in `judgements/`, `results/`, PyTorch (CPU build) and the embedding model `intfloat/multilingual-e5-small`, which is downloaded once at build time. Apertus is reached through `LLM_BASE_URL`.

**Local.** `cp .env.example .env`, fill in `LLM_API_KEY`, then:

```
docker compose up --build      # open http://localhost:8000
```

The pipeline commands of «Getting started» run in the same container:

```
docker compose exec app python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-07-01 --to 2026-07-31
docker compose exec app python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q3 --output results/triage_q3_2026.json
docker compose exec app python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage_q3_2026.json
```

`data/` is mounted from the host, so the crawled judgements and the summaries database (`data/processed/summaries.db`) survive a restart. The port is published on this machine only (`127.0.0.1`), because the app has no login; `APP_BIND=0.0.0.0` opens it to the network and `APP_PORT=8080` changes the port. Without a key the app still starts; only generating a new summary needs it.

**On-premise or air-gapped.** Build the image on a machine with internet access (`docker compose build`), move it with `docker save coffee-exe-suva | gzip > coffee-exe-suva.tar.gz` and `docker load`, and copy `data/` next to `docker-compose.yml`. Set `LLM_BASE_URL` to an Apertus v1.5 endpoint inside the network (any server with an OpenAI-compatible API). The container then opens no connection except to that endpoint, and the web UI loads no external files. The embedding model is already in the image, so nothing is downloaded at runtime; only fetching new judgements needs the court's site.

**Azure (not tested).** Nothing in the code or the image is tied to a cloud provider, and all settings are environment variables. To move it, push the image to Azure Container Registry, run it as one replica on Azure Container Apps and set `LLM_NAME`, `LLM_BASE_URL` and `LLM_API_KEY` as secrets. `/app/data` needs persistent storage. The summaries database is a SQLite file in WAL mode: that allows one replica only, and whether it works on a network file share such as Azure Files is not verified.

## License

All Hack Apertus projects are open-sourced. Please check our Terms & Conditions for specific licensing details (6. What you build is open source): https://hackapertus.ch/terms-and-conditions

## Support

### FAQ
💡 https://hackapertus.ch/faq

### Contact

💬 In case you have questions, join the conversation on Discord. Alternatively, send an email to “hello@hackapertus.ch”.
