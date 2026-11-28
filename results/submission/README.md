# Submission folder

Everything we hand in, in one place. Assembled by

    python -m hack_apertus_coffee_exe.present.submission --generate

which regenerates the summaries where needed (Apertus gateway), copies the report PDF, checks
the video link, builds the ZIP and prints a checklist. Without `--generate` it only assembles.

| File | What | Where it goes on sprintd |
|---|---|---|
| `recall_list.txt` | the 103 relevant judgements of Q2/2026 in rank order, Suva's format (`8C_137/2025, 8C_17/2026, ...`); the same as `results/recall_q2_2026.txt`. With the 8 almost relevant ones and details per judgement: `results/tables/recall_list.csv` | in the repo (repo URL) |
| `summaries_top10_2026-06.md` / `.json` | the 10 best-ranked judgements of June 2026 with their summary in German and French | in the repo (repo URL) |
| `summaries_top10_2026-06.pdf` | the same summaries as `summaries_top10_2026-06.md`, as a PDF for reading and printing. Not built by the command: export it again from the `.md` whenever the summaries change | in the repo (repo URL) |
| `Coffee_Exe_Report.pdf` | the technical report: copy of `Coffee_Exe_Report.pdf` in the repo root, same name (the organisers want the report there as `TeamName_Report.pdf`; the original stays there) | in the ZIP |
| `pitch_deck.pdf` | pitch deck, max. 10 slides, with the slide on how Apertus is used. **To come**: put it here | in the ZIP |
| `submission.zip` | exactly `pitch_deck.pdf` + `Coffee_Exe_Report.pdf`, built by the command once both are here | upload as the project ZIP |
| `video_url.txt` | URL of the demo video, max. 2 minutes. **To come**: paste it here **and** into the Demo field at the top of `technical_report.md` | video URL field |

Before clicking «Submit project»: run the command once more, so the ZIP has the latest report
and deck, and every line of the checklist is ticked.
