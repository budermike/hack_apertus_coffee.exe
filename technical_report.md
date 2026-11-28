# Technical report — Urteilsradar, Team Coffee.exe

A deeper write-up than the README: what we built, how it works, and what the
numbers say.

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

Headline result, measured on the two quarters where Suva's selection is known (59
selected judgements) and always with the calibration from the *other* quarter: the list
finds **49 of 59** (recall 83 %, F2 0.74) at the F2-optimal threshold and **58 of 59**
(recall 98 %, F2 0.68) at the setting we ship, a recall target of 95 %, where the list has 193
instead of 97 entries out of about 3900 published judgements. For the quarter of the task,
Q2/2026, the list has 103 entries out of 2096 judgements. Suva's selection for Q2 is not known to us, so recall on
Q2 itself is not measured. The summaries (Apertus 70B) reach a mean of 3.8 of 5 when an Apertus judge compares
them with Suva's own texts by a fixed rubric (section 5).

Beyond the task (bonus «better customer experience»): every score comes with the criteria and verbatim quotes it
rests on; lawyers edit and release summaries (the knowledge base keeps every version); a click on «relevant» /
«not relevant» is learned for similar judgements without ever removing a judgement from the list; full-text search
over all stored summaries; the interface works in German and French.

## 2. Architecture

```
entscheidsuche.ch (mirror of bger.ch)
   │ crawl: one JSON per judgement, only missing days
   ▼
data/raw/  ──►  Vector-DB (SQLite + FTS5, e5 embeddings)      similar earlier Suva cases
   │
   ▼
triage: Apertus v1.5 70B, pass 1 (criteria with quotes, score), pass 2 (use for accident
        insurance, practical rule, score) ─► calibration (logistic regression) ─► list
   │ judgements on the list automatically, others on request
   ▼
summary: Apertus v1.5, facts ─► German text / French text ─► check by rules ─► SQLite
   │
   ▼
backend (Python standard library HTTP server, /api/...)  ◄──►  web UI (plain JavaScript)
```

| Stage | Code | What it does |
|---|---|---|
| Crawl | `src/hack_apertus_coffee_exe/crawl/` | Fetches judgements per day, stores text and metadata with a hash, fills the Vector-DB |
| Triage | `.../triage/` | Candidates (social-law divisions), two Apertus passes, calibration, list threshold |
| Summary | `.../summary/` | Facts, German and French text written separately (no translation), check by rules, versions in SQLite |
| Present | `.../summary/api.py`, `.../present/`, `src/presentation/` | API and UI: list, reasons with quotes, summaries, editing, decisions, search |
| Evaluation | `.../eval/` | Metrics only, no model judges; results in `results/` |

**Relevance is computed, not guessed.** Apertus gives two scores per judgement. A logistic
regression with four parameters (weight of each score, one intercept for accident
insurance, one for the other areas) turns them into a relevance value `p`. It is fitted
only on quarters before the scored one.

**The thresholds are derived, not set by hand.** The list threshold is the highest
value of `p` at which 95 % of the judgements Suva actually selected in the labelled quarters
are on the list (`target_recall` in `configs/triage.json`); yellow starts where all of them
are at least yellow (`yellow_recall` 1.0). The value is shown on a 0–100 scale where 60 is
the list threshold and 40 the start of yellow. Every calibration stores why its thresholds
sit where they do: which rule, how many known Suva cases lie above each threshold, how many
candidates, and the known case right at the edge (`basis` in
`results/tables/relevance_calibration_q2_2026.json`). The UI shows that reason in German or
French next to the score, and `triage.cli relevance` prints it. For Q2: 58 of the 59 known
cases above the list threshold (the target is 95 %, ties on Apertus' coarse scores put one
more above it), all 59 above the yellow threshold. The earlier rule, the F2 threshold times a
hand-set `list_factor` 0.3, is still available as a fallback and described as hand-set when
in use.

**Lawyers' decisions are learned from.** A click on «Für Suva relevant» / «Nicht relevant»
is a training example. The calibration stays unchanged; on top of it one correction per
legal area and per criterion is learned from all decisions (logistic regression with the
calibrated value as offset and a normal prior). Judgements that share area or criteria
with a decided one move with it. The list is protected: learning can add judgements and
reorder the list, only the lawyer's own decision takes a judgement off it (section 5 says
why).

**What the UI offers beyond the task:** reasons also for judgements that are *not*
selected; quotes checked against the judgement text (exact / approximate / missing);
editing with one edited version per language, the other language following through
Apertus, and a reset to the generated text; a searchable database of all summaries; the
stored judgement text readable inside the application; a German and a French interface
in which subjects, quotes and the basis of a summary are shown in the chosen language
(carried over by Apertus in legal wording, a carried-over quote marked as such).

### Target architecture (mandatory)

The solution is deployable **a) on-premise**, **b) air-gapped** and **c) on a sovereign
Swiss cloud**. One container (`Dockerfile`, `docker-compose.yml`) holds the backend and
the UI; all settings are environment variables; data lives in one mounted folder
(`data/`: judgements, Vector-DB, SQLite).

| Dependency | Build time | Runtime |
|---|---|---|
| Apertus v1.5 endpoint (OpenAI-compatible) | – | yes: `LLM_BASE_URL`. During the hackathon the CSCS/Keera gateway; on-premise any server that serves the open weights |
| Python packages (`requirements.txt`) | yes (PyPI) | no network |
| Embedding model `intfloat/multilingual-e5-small` (open weights) | download once from Hugging Face | only when new judgements are taken into the Vector-DB; run locally |
| entscheidsuche.ch | – | only when a new period is fetched |
| Proprietary AI services | none | none |

Verified: the container starts and is healthy within seconds, and with **no network at
all** it serves the UI, the Q2 list, the reasons, stored summaries, editing, decisions
with learning, and the search. Not possible without network, by nature: Apertus calls and
fetching new judgements. Known gap: the image has no PyTorch, so a *new* period cannot be
taken into the Vector-DB inside the container; it has to be prepared outside and copied
in with `data/`. Azure: nothing is tied to a cloud provider (container, environment
variables, OpenAI-compatible endpoint); the steps are in `README.md`, not tested.

## 3. Use of Apertus

Apertus v1.5 performs the core task in all places; no other language model is called at
runtime.

| Task | Model (gateway ID) | How |
|---|---|---|
| Relevance, pass 1 and 2 | `apertus-v1-5-70b` (default of `triage.cli relevance`) | Inference. Prompt with Suva's criteria (K1–K5 for, N1–N3 against) and similar earlier judgements with Suva's decision; answer as JSON with quotes |
| Summaries German / French | `SUMMARY_MODEL` (default `apertus-v1-5-70b`) | Inference in two steps (`summary.generate.Summarizer`): name the core question and the 1–3 decisive considerations (JSON), then write each language from the wording of those considerations with a style profile measured on Suva's 55 texts (`prompts/style_de.md`, `style_fr.md`, `glossary.md`), three Suva examples marked as other cases, and a rule-based block on who appealed and who won. A check without a model follows (cited considerations and articles exist, outcome and remand agree with the operative part, length, paragraphs); on a fault the text is written once more |
| Other language after an edit | same as summaries | Inference: brings the untouched language in line with the lawyer's edit |
| Reasons in French | same as relevance | Inference, once per judgement, cached |
| Subjects, quotes and summary basis in the language of the interface | same as relevance | Inference in batches with the glossary: legal wording of the target language, not word for word; stored after the first time |

No fine-tuning. `temperature = 0` with a fixed seed; only a relevance call whose answer
could not be read is repeated once with 0.4. Calls go through one wrapper per stage (`triage/llm.py`, `summary/llm.py`) with
timeouts and retries; relevance answers are cached by request hash. Supporting model:
`intfloat/multilingual-e5-small` (MIT, open weights, local) for the Vector-DB. Compared
and not adopted: the reasoning mode of the 70B model (50 instead of 49 of 59 at the
F2 threshold, no difference at the shipped setting; `results/tables/thinking_vs_normal_relevanz.csv`).

Models used in development only (coding assistants: Claude Code with Claude Opus 5.5 and
Claude Fable 5.2, Claude Sonnet 5.5, Antigravity with Gemini) are listed with their roles
in `docs/MODELS.md`. They wrote code, tests and documentation; they score, select and
summarise nothing at runtime. One model judges in the evaluation: Apertus 70B grades the
summaries against Suva's texts (open weights, as required; section 5).

## 4. Data

| Data | Source | In the repository |
|---|---|---|
| 6052 Federal Supreme Court judgements, October 2025 to June 2026 (Q4/2025: 1930, Q1/2026: 2004, Q2/2026: 2096) | entscheidsuche.ch, a public mirror of bger.ch | yes (`data/raw/`), public court decisions, parties anonymised by the court |
| Suva's selection for Q4/2025 and Q1/2026 and 55 example summaries | challenge partner | `judgements/`; whether they may stay in a public repository is the partner's decision |
| Scores, lists, evaluation tables | produced by this project | `results/` |

The task names bger.ch. Its search is protected against automated access, so the crawler
reads the mirror, which carries the same decisions with the link to bger.ch; the crawler
for bger.ch exists (`--source bger`). «The last three months» is Q2/2026 (1 April to 30
June 2026), the newest complete quarter. No personal data beyond what the court publishes;
no data from human subjects.

## 5. Evaluation and limitations

All numbers come from `results/` and can be recomputed without a model call.

| What | Result | Source |
|---|---|---|
| Similarity only, no Apertus, Q1/2026 | recall 74 %, precision 46 %, F2 0.66 (list of 50) | `results/eval.md` |
| Two Apertus passes, calibration from the other quarter, F2 threshold | 49 of 59 found, recall 83 % (95 % interval 73–92 %), F2 0.74, list 97 | `results/relevance_eval.md` |
| Same, shipped setting (recall target 95 %) | 58 of 59 found, F2 0.68, list 193 | `results/relevance_eval.md` |
| Same, earlier setting (`list_factor` 0.3) | 59 of 59 found, F2 0.64, list 226 | `results/relevance_eval.md` |
| Q2/2026 as shipped | 410 candidates, 187 scored by Apertus, 103 on the list, 8 just below | `results/triage_q2_2026.json` |
| Summaries, 4 models × 4 prompt versions, 20 development cases, judge Apertus 70B (rubric 1–5) | only 70B reaches ≥ 3.5 in every criterion (70B 4.2); best 8B variant 3.2 (8B thinking) | `notebooks/summary/02_modelle_prompts.ipynb` |
| Summaries as shipped (70B), all 55 Suva cases | core message 4.0, outcome 4.1, faithfulness 4.0, style 3.6, language 4.3, overall 3.8; on the 35 held-out cases overall 3.5, style 3.3 | `results/tables/summary_bestaetigung_70b.csv` |
| Learning from decisions, simulated with Suva's decisions | unprotected: list shrinks, recall falls by up to 67 points in single runs; protected: recall never falls in the 80 runs (0 to +1.3 points); ranking not measurably better | `results/reports/feedback_learning.md` |

Limitations, stated plainly:

- **Recall on Q2 is unknown**, because Suva's selection for Q2 is not available to us. The 100 % is measured on two quarters with 59 selected judgements; one or two judgements more or less are chance. The criteria in the prompt were written with both labelled quarters in view, so the values are rather too favourable.
- **The list is long on purpose:** 103 of 2096 in Q2, where Suva selects about 30. We accept reading more to miss almost nothing: in the hold-out 1 of 59 Suva judgements was missed. On the calibration quarters the one known case below the list threshold (9C_65/2025) is yellow, so still in view.
- **Summaries are graded by a model, not by Suva's lawyers.** The judge is Apertus 70B, which may favour texts of its own model; a rule-based style measure (length, paragraphs, opening, outcome sentence, wording) points the same way. The new prompt is clearly closer to Suva's form (0.51 vs 0.39 on that measure) but, on the 35 cases it was not tuned on, not better in content than the old prompt with the same model (3.46 vs 3.72 overall); style stays the weakest criterion. About one text in five scores 2 or less, mostly because it picks a different core question than Suva.
- Only judgements of the social-law divisions are candidates, and Apertus scores accident, invalidity and military insurance. A relevant judgement from another division is not found.
- Fetching a new period needs the embedding library, which the container does not contain; quotes marked «approximate» or «missing» must be checked by the reader; the application has no login and is meant to run inside a protected network.

## 6. Reproducibility and how to run it

Requirements: Python 3.13, about 2 GB of disk space for the Python environment, access to
an Apertus v1.5 endpoint. No GPU. Versions are pinned in `requirements.txt` / `requirements-dev.txt`.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # set LLM_API_KEY; LLM_BASE_URL and LLM_NAME have defaults
pytest                          # over 300 tests, no network, under 15 seconds
python src/keera_example.py     # one test call to the gateway

# web application on the shipped Q2 results (http://127.0.0.1:8000)
python -m hack_apertus_coffee_exe.summary.api --static src/presentation --triage results/triage_q2_2026.json

# or in a container
docker compose up --build
```

The whole pipeline from scratch (several hours of Apertus calls on the shared gateway):

```
python -m hack_apertus_coffee_exe.crawl.ensure --from 2026-04-01 --to 2026-06-30
python -m hack_apertus_coffee_exe.triage.cli relevance --period 2026-Q2 --output results/triage_q2_2026.json
python -m hack_apertus_coffee_exe.summary.run batch --triage results/triage_q2_2026.json
```

The numbers of section 5 without any model call:

```
python -m hack_apertus_coffee_exe.eval.relevance_eval      # results/relevance_eval.md
python -m hack_apertus_coffee_exe.eval.feedback_sim        # results/reports/feedback_learning.md
jupyter nbconvert --execute --to notebook notebooks/summary/02_modelle_prompts.ipynb   # summary tables from the stored answers
```

`temperature = 0` and a fixed seed in the Apertus calls, fixed seeds in the simulation (50
draws, seeds 0–49) and in the bootstrap. Answers of a shared gateway are still not
guaranteed to be identical between runs; the score files of our runs are in
`results/tables/relevance_scores_*.json`, so every table can be recomputed from them.
Commit: the state of `main` at submission.

## License

Creative Commons Attribution 4.0 (CC-BY-4.0). All HackApertus projects are open-sourced.

## References

- Swiss AI Initiative, Apertus v1.5: <https://huggingface.co/swiss-ai/Apertus-v1.5-8B>, <https://huggingface.co/swiss-ai/Apertus-v1.5-70B>
- Wang et al., Multilingual E5 Text Embeddings, 2024: <https://huggingface.co/intfloat/multilingual-e5-small>
- Federal Supreme Court decisions: <https://www.bger.ch>, mirror <https://entscheidsuche.ch>
- Project documents: `docs/ARCHITECTURE.md`, `docs/MODELS.md`, `docs/DECISIONS.md`, `docs/ISSUE_RECALL.md`, `docs/SCHEMA.md`
