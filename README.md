# Hack Apertus Zurich — Challenge Partner: Suva

## Our solution: Urteilsradar (Team Coffee.exe)

The Federal Supreme Court publishes about 2000 judgements per quarter; Suva's legal specialists need the roughly 30 that matter for accident insurance, summarised in German and French. **Urteilsradar** fetches the judgements, lets **Apertus v1.5** score every candidate in two passes and explain the score with quotes from the judgement, writes the summaries in Suva's style in both languages, and shows everything in a web interface where lawyers search, read, correct and decide.

- Measured on the two quarters with a known Suva selection, always with the calibration of the other quarter: 49 of 59 selected judgements found at the F2-optimal threshold, 58 of 59 at the shipped setting (a recall target of 95 %, longer list). Why each threshold sits where it does is shown next to the score in the UI. Details and limits: [`technical_report.md`](technical_report.md).
- How it is built: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Every model used: [`docs/MODELS.md`](docs/MODELS.md). Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md).
- Demo video and pitch deck: linked in `technical_report.md` and the submission form.

### Run it

Python 3.13. The environment variables are the ones named under [Environment](#environment): `LLM_NAME`, `LLM_BASE_URL`, `LLM_API_KEY`.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # set LLM_API_KEY
pytest                          # unit tests, no network
python src/keera_example.py     # one test call to the Apertus gateway

# web application on the shipped results for Q2/2026: http://127.0.0.1:8000
python -m hack_apertus_coffee_exe.summary.api --static src/presentation --triage results/triage_q2_2026.json
```

Enter 01.04.2026 to 30.06.2026 as period and search. Judgements on the list are summarised automatically (this calls Apertus); without a key the list, the reasons and the stored texts still work. In a container: `docker compose up --build`, see [Deployment](#deployment).

The pipeline from scratch (several hours of Apertus calls):

```
python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-04-01 --to 2026-06-30
python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2 --output results/triage_q2_2026.json
python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage_q2_2026.json
```

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
| `configs/` | Settings: paths, crawl source, thresholds |
| `prompts/` | Style profiles and glossary for the summaries |
| `judgements/` | Suva's example summaries and selection lists (reference data of the challenge partner) |
| `data/` | `raw/`: fetched judgements; `processed/`: Vector-DB, caches, summaries database |
| `results/` | Scores, lists, evaluation tables and reports |
| `tests/` | Unit tests (no network) |
| `docs/` | Architecture, model register, decisions, schema, audit |
| `Dockerfile`, `docker-compose.yml` | Container for backend and web interface |

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

See [Run it](#run-it) at the top of this file; the same instructions with the commands for the evaluation are in section 6 of [`technical_report.md`](technical_report.md).

## Deployment

The backend and the web UI run in one container (`Dockerfile`, `docker-compose.yml`). The image holds the code, the UI, `configs/`, `prompts/`, the Suva examples in `judgements/` and `results/`, but no model and no PyTorch: Apertus is reached through `LLM_BASE_URL`, and the app as served needs no embedding model.

**Local.** `cp .env.example .env`, fill in `LLM_API_KEY`, then:

```
docker compose up --build      # open http://localhost:8000
```

`data/` is mounted from the host, so the crawled judgements and the summaries database (`data/processed/summaries.db`) survive a restart. The port is published on this machine only (`127.0.0.1`), because the app has no login; `APP_BIND=0.0.0.0` opens it to the network and `APP_PORT=8080` changes the port. Without a key the app still starts; only generating a new summary needs it.

**On-premise or air-gapped.** Build the image on a machine with internet access (`docker compose build`), move it with `docker save coffee-exe-suva | gzip > coffee-exe-suva.tar.gz` and `docker load`, and copy `data/` next to `docker-compose.yml`. Set `LLM_BASE_URL` to an Apertus v1.5 endpoint inside the network (any server with an OpenAI-compatible API). The container then opens no connection except to that endpoint, and the web UI loads no external files. Crawling and embedding new judgements is not part of the image: it needs internet access and the embedding model from `requirements-dev.txt`.

**Azure (not tested).** Nothing in the code or the image is tied to a cloud provider, and all settings are environment variables. To move it, push the image to Azure Container Registry, run it as one replica on Azure Container Apps and set `LLM_NAME`, `LLM_BASE_URL` and `LLM_API_KEY` as secrets. `/app/data` needs persistent storage. The summaries database is a SQLite file in WAL mode: that allows one replica only, and whether it works on a network file share such as Azure Files is not verified.

## License

All Hack Apertus projects are open-sourced. Please check our Terms & Conditions for specific licensing details (6. What you build is open source): https://hackapertus.ch/terms-and-conditions

## Support

### FAQ
💡 https://hackapertus.ch/faq

### Contact

💬 In case you have questions, join the conversation on Discord. Alternatively, send an email to “hello@hackapertus.ch”.
