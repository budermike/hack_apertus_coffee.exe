# Pitch outline (max. 10 slides, PDF; 3–5 min)

Organisers' rule: the pitch deck is submitted as PDF with **at most 10 slides** (`README.md`, «Pitch deck»).
Numbers below come from `technical_report.md` and `results/`; figures are ready in `results/figures/` (1920 × 1080).

| # | Slide | Content | Figure / source |
|---|---|---|---|
| 1 | Title | Urteilsradar · Team Coffee.exe · Hack Apertus Zurich, challenge Suva | – |
| 2 | Problem | ~2000 Federal Supreme Court judgements per quarter; Suva needs the ~30 that matter for accident insurance, summarised in German and French | `challenge_description.md` |
| 3 | Solution | Crawl → Apertus scores every candidate in two passes with quotes → summaries in Suva's style DE/FR → web UI to search, read, correct, decide | `docs/ARCHITECTURE.md` (data flow) |
| 4 | Live demo | One happy path: Q2/2026 list, one judgement with criteria and quotes, summary DE/FR, edit and release | demo checklist below |
| 5 | Finding the right judgements | With Apertus 70B every Suva case is among the first ~120 judgements; today (similarity only) only from rank 1847 | `results/figures/pitch_1_recall_kurven.png` |
| 6 | Less to read | From 2039 to 186 judgements to read to find all 59 Suva cases (two labelled quarters) | `results/figures/pitch_2_lesemenge_alle_faelle.png` |
| 7 | Model choice | 70B with two passes gives the most precise list; 8B is ~15× cheaper and still far better than today; thinking mode brings nothing measurable | `pitch_3_treffsicherheit_liste.png`, `pitch_4_aufwand_nutzen.png` |
| 8 | Summaries in Suva's style | Style profile measured on Suva's 55 texts; 4 models × 4 prompts compared; only 70B reaches ≥ 3.5 in every criterion; overall 3.8 / 5 against Suva's own texts | `results/figures/summary_modelle_prompts.png` |
| 9 | Why Apertus, sovereign | Only Apertus v1.5 and an open-weights embedding model at runtime; runs on-premise, air-gapped or on a Swiss cloud, one container, configuration by environment variables (also Azure) | `technical_report.md` §2 «Target architecture» |
| 10 | Honest limits and team | Recall on Q2 unknown (no Suva labels); list is long on purpose; summaries graded by a model, not by Suva lawyers · Mike, Abi, Linus, Ben | `technical_report.md` §5 |

## Demo checklist
- [ ] Demo runs on the shipped results: `python -m hack_apertus_coffee_exe.summary.api --static src/presentation --triage results/triage_q2_2026.json`
- [ ] Judgement for the demo chosen and its summary generated beforehand (no live gateway wait)
- [ ] Backup screen recording (also usable as the demo video, max. 2 min)
- [ ] Wi-Fi-independent fallback (container or local run without gateway calls)
- [ ] Who speaks, who clicks
