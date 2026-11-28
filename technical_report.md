# Technical report — Urteilsradar, Team Coffee.exe

- **Challenge partner:** Suva
- **Challenge:** Identification of relevant Federal Court judgements and preparation of a legal summary
- **Event:** On-site Zurich
- **Team:** Coffee.exe - Mike (project management), Abi (backend), Linus (frontend and demo), Ben (backend and evaluation)
- **Demo:** link to the video (max. 2 minutes) is added by the team with the submission

## 1. Summary

The Federal Supreme Court publishes about 2000 judgements per quarter. Suva's legal
specialists have to find the few that matter for accident insurance (about 30 per quarter)
and summarise them in German and French. **Urteilsradar** fetches the judgements, lets
Apertus v1.5 score each candidate in two passes and explain the score with quotes from the
judgement, writes the summaries in Suva's style in both languages, and shows everything in
a web interface where lawyers search, read, correct and decide.

Measured on the two quarters where Suva's selection is known (59 selected judgements), always
with the calibration from the *other* quarter, the list finds **49 of 59** (recall 83 %, F2 0.74)
at the F2-optimal threshold and **58 of 59** (recall 98 %, F2 0.68) at the setting we ship
(recall target 95 %; 193 instead of 97 entries out of about 3900 judgements). For Q2/2026, the
quarter of the task, the list has 103 entries out of 2096; Suva's selection for Q2 is not known,
so recall on Q2 is not measured. The summaries (Apertus 70B) reach 3.8 of 5 when an Apertus
judge compares them with Suva's own texts by a fixed rubric.

**Beyond the task** (bonus «better customer experience»): reasons with verbatim quotes also for
judgements that are *not* selected, each quote checked against the judgement text; editing
of summaries, the other language following through Apertus, every version kept; lawyers'
«relevant» / «not relevant» decisions learned for similar judgements; full-text search over
all summaries; the stored judgement text readable inside the application; a German and a
French interface.

## 2. Architecture

```
entscheidsuche.ch (mirror of bger.ch)
   │ crawl: one JSON per judgement, only missing days
   ▼
data/raw/  ──►  Vector-DB (SQLite + FTS5, e5 embeddings, used by the analyses)
   │
   ▼
triage: Apertus v1.5 70B, pass 1 (criteria with quotes, score), pass 2 (use for accident
        insurance, practical rule, score) ─► calibration (logistic regression) ─► list
   │ judgements on the list queued after a search, others on request
   ▼
summary: Apertus v1.5 70B, core question ─► German text / French text ─► check by rules ─► SQLite
   │
   ▼
backend (Python standard library HTTP server, /api/...)  ◄──►  web UI (plain JavaScript)
```

**Relevance is computed, not guessed.** Apertus gives two scores per judgement. A logistic
regression with four parameters (one weight per score, one intercept for accident insurance,
one for the other areas) turns them into a relevance value, fitted only on quarters other
than the scored one.

**Thresholds are derived, not set by hand.** The list threshold is the highest value at which
95 % of the judgements Suva selected in the labelled quarters are on the list; yellow starts
where all of them are at least yellow. Each calibration stores why its thresholds sit where
they do, and the UI shows that reason next to the score.

**Lawyers' decisions are learned from.** A click on «Für Suva relevant» / «Nicht relevant» is a
training example: on top of the unchanged calibration one correction per legal area and
criterion is learned, so similar judgements move with a decided one. Learning can add
judgements to the list and reorder it; only the lawyer's own decision takes one off.

### Target architecture (mandatory)

The solution is deployable **a) on-premise**, **b) air-gapped** and **c) on a sovereign Swiss
cloud**. One container (`Dockerfile`, `docker-compose.yml`) holds backend, UI and the whole pipeline; all settings
are environment variables; data lives in one mounted folder (`data/`).

| Dependency | Build time | Runtime |
|---|---|---|
| Apertus v1.5 endpoint (OpenAI-compatible, `LLM_BASE_URL`) | – | yes; on-premise any server with the open weights |
| Python packages (`requirements.txt`) | PyPI | no network |
| Embedding model `intfloat/multilingual-e5-small` (open weights) | Hugging Face, once, into the image | local, CPU; when `crawl.ensure` embeds new judgements into the Vector-DB |
| entscheidsuche.ch | – | only when a new period is fetched |
| Proprietary AI services | none | none |

With **no network at all** the container serves the UI, the Q2 list, reasons, stored
summaries, editing, decisions and search; only Apertus calls and fetching new judgements need
a connection. The image contains everything, including PyTorch (CPU) and the embedding model, so
the whole pipeline from crawling to summaries runs inside the container. Azure: nothing is tied to a cloud provider; the steps are in `README.md`, not
tested.

## 3. Use of Apertus

Apertus v1.5 performs the core task everywhere; no other language model is called at runtime.

| Task | Model | How |
|---|---|---|
| Relevance, two passes | `apertus-v1-5-70b` | Prompt with Suva's criteria (K1–K5 for, N1–N3 against) and similar earlier judgements with Suva's decision; JSON answer with quotes |
| Summaries German / French | `SUMMARY_MODEL` (default `apertus-v1-5-70b`) | First the core question and the 1–3 decisive considerations (JSON), then each language written from their wording with a style profile measured on Suva's 55 texts, a glossary, three Suva examples marked as other cases and a rule-based block on who appealed and who won; a check by rules (cited considerations and articles exist, outcome agrees with the operative part) triggers one rewrite. After an edit, Apertus aligns the other language |
| DE/FR check | `SUMMARY_MODEL` (default `apertus-v1-5-70b`) | Lengths compared by rules (at most 30 % apart, in characters); the language out of bounds is rewritten once. Optional, off by default: a reviewer that looks for contradictions and omissions in two passes, and a referee that decides from the judgement which language is right (`summary/crosscheck.py`, `SUMMARY_CROSSCHECK=1`). In a first test it tripled the time and its repair cut a correct French text in half, so it is off |
| Interface language | `apertus-v1-5-70b` | Reasons, subjects and quotes carried over into German or French in legal wording, stored after the first time |

No fine-tuning. `temperature = 0` with a fixed seed; calls go through one wrapper per stage with
timeouts and retries, answers are cached. Supporting model: `intfloat/multilingual-e5-small`
(MIT, open weights, local). All four Apertus variants (8B, 70B, each with and without
reasoning mode) were compared; the reasoning mode brought no measurable gain. Coding assistants
used in development (Claude Code with Claude Opus 5.5 and Claude Fable 5.2, Claude Sonnet 5.5,
Antigravity with Gemini and Gemini 4 Hydrogen) are listed with their roles in `docs/MODELS.md`;
they wrote code, prompts and texts and do nothing at runtime.

## 4. Data

| Data | Source | In the repository |
|---|---|---|
| 6052 judgements, October 2025 to June 2026 (Q4/2025: 1930, Q1/2026: 2004, Q2/2026: 2096) | entscheidsuche.ch, public mirror of bger.ch | yes, public court decisions, anonymised by the court |
| Suva's selection for Q4/2025 and Q1/2026, 55 example summaries | challenge partner | `judgements/`, subject to the partner's permission |
| Scores, lists, evaluation tables | this project | `results/` |

The task names bger.ch; its search is protected against automated access, so the crawler reads
the mirror, which carries the same decisions with the link to bger.ch (`--source bger` exists).
«The last three months» is Q2/2026 (1 April to 30 June 2026). No personal data beyond what the
court publishes.

## 5. Evaluation and limitations

All numbers can be recomputed from `results/` without a model call.

| What | Result | Source |
|---|---|---|
| Similarity only, no Apertus, Q1/2026 | recall 74 %, precision 46 %, F2 0.66, list of 50 | `results/eval.md` |
| Two Apertus passes, F2 threshold | 49 of 59, recall 83 % (95 % interval 73–92 %), F2 0.74, list 97 | `results/relevance_eval.md` |
| Same, shipped setting (recall target 95 %) | 58 of 59, F2 0.68, list 193 | `results/relevance_eval.md` |
| Q2/2026 as shipped | 410 candidates, 187 scored, 103 on the list | `results/triage_q2_2026.json` |
| Summaries, 4 models × 4 prompt versions, 20 cases, Apertus 70B as judge (1–5) | only 70B reaches ≥ 3.5 in every criterion (4.2); best 8B variant 3.2 | `notebooks/summary/02_modelle_prompts.ipynb` |
| Summaries as shipped (70B), all 55 Suva cases | core message 4.0, outcome 4.1, faithfulness 4.0, style 3.6, language 4.3, overall 3.8; 35 held-out cases: overall 3.5, style 3.3 | `results/tables/summary_bestaetigung_70b.csv` |
| Learning from decisions, simulated | protected list: recall never falls in 80 runs; ranking not measurably better | `results/reports/feedback_learning.md` |

Limitations:

- **Recall on Q2 is unknown** (no Suva labels for Q2). Two quarters with 59 judgements are few; the prompt criteria were written with both in view, so the values are rather favourable.
- **The list is long on purpose** (103 of 2096 in Q2, Suva selects about 30): reading more to miss almost nothing.
- **Summaries are graded by a model, not by Suva's lawyers.** The judge is Apertus 70B and may favour its own model; a rule-based style measure points the same way. On the 35 cases the prompt was not tuned on, the new prompt is closer to Suva's form but not better in content than the old one; about one text in five scores 2 or less, mostly for picking a different core question than Suva.
- Only the social-law divisions are candidates; quotes marked «approximate» or «missing» need checking; the application has no login and belongs inside a protected network.

## 6. Reproducibility and how to run it

Python 3.13, about 2 GB of disk space, no GPU, access to an Apertus v1.5 endpoint. Versions are
pinned in `requirements.txt` / `requirements-dev.txt`.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set LLM_API_KEY
pytest                        # unit tests, no network
python -m hack_apertus_coffee_exe.summary.api --static src/presentation --triage results/triage_q2_2026.json
docker compose up --build     # same application in a container, http://localhost:8000
```

Whole pipeline (several hours of Apertus calls) and the evaluation (no model call):

```
python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-04-01 --to 2026-06-30
python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2 --output results/triage_q2_2026.json
python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage_q2_2026.json
python -m hack_apertus_coffee_exe.eval.relevance_eval
python -m hack_apertus_coffee_exe.eval.feedback_sim
```

Fixed seeds and `temperature = 0`; a shared gateway may still answer differently between runs,
so all score files of our runs are stored in `results/tables/`. Full instructions: `README.md`.
Commit: the state of `main` at submission.

## License

Creative Commons Attribution 4.0 (CC-BY-4.0). All HackApertus projects are open-sourced.

## References

- Swiss AI Initiative, Apertus v1.5: <https://huggingface.co/swiss-ai/Apertus-v1.5-8B>, <https://huggingface.co/swiss-ai/Apertus-v1.5-70B>
- Wang et al., Multilingual E5 Text Embeddings, 2024: <https://huggingface.co/intfloat/multilingual-e5-small>
- Federal Supreme Court decisions: <https://www.bger.ch>, mirror <https://entscheidsuche.ch>
- Project documents: `docs/ARCHITECTURE.md`, `docs/MODELS.md`, `docs/DECISIONS.md`, `docs/ISSUE_RECALL.md`
