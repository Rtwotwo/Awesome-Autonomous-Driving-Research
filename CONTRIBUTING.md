# Contributing

Contributions of papers, datasets, benchmarks, and reproducible results are welcome. Keep each pull request focused on one type of change and cite primary sources that maintainers can verify.

## Adding a Paper

The source of truth is `data/papers.json`. Add a verified paper to the appropriate track, together with a repository-audit record and an explicit citation availability record. Then run `npm run readme` and `npm run validate`. An optional Zotero BibTeX import preserves existing curated records:

```bash
node scripts/sync_zotero.mjs path/to/Autonomous-Driving.bib
```

An eligible paper must:

- directly address end-to-end autonomous driving, driving world models, vision-language-action driving, recursive self-improvement, or perception and supporting driving research;
- provide an arXiv, conference, or publisher page;
- have a title, year, and source that can be independently verified;
- use an author-maintained project or repository when code links are included.

Use one primary track per paper: `e2e`, `vla`, `world-model`, `rsi`, or `perception-other`. Keep each track newest first and do not add topic subcategories. A promised release can be `pending` without a repository URL; mark a release as `released` only after verifying actual implementation files. Scope-limited releases use `codeScope: "partial"` with a short explanation. Discovery articles are leads; cite primary paper and author sources for catalog facts.

## Maintaining Industry Research News

Backfill company reports from **January 1, 2026** onward (`coverageStart: "2026-01-01"`), not only the last seven days. The rolling seven-day window is for fresh discovery; also check earlier unselected reports within this coverage period. Keep the earliest paper submission date distinct from a later company announcement. Record backfill sources and unresolved coverage gaps in `backfillRuns`; do not claim exhaustive coverage when evidence is incomplete.

Maintain `data/industry-news.json` alongside the paper catalog. Track automotive research teams worldwide, including Xiaomi, XPeng, NIO, Momenta, Horizon Robotics, ByteDance, Alibaba, ZYT, NVIDIA and Tesla. Technical report entries reference verified papers in `data/papers.json`. Use `predecessorPaperIds` to link documented earlier papers in the same series, including publications before the News window.

Use the first arXiv submission date shown by the primary record (`dateType: "arxiv-first-submission"`). Reports without an arXiv record can use a verifiable first official release date (`dateType: "official-report-release"`) and a link to the full report. Record `checkedAt` separately. Each company needs primary affiliation evidence from the paper or an official author/company resource. A model being used as a backbone, a sponsorship, or a similarly named method is not sufficient affiliation evidence.

For reports, set `publicationType: "company-technical-report"` and provide `reportEvidence` for each company: an official team-signed report, a company announcement, or the official team's system-report release. Label public drafts explicitly. Individual or intern-led projects without official team ownership do not qualify; do not infer internship status from names, seniority or university affiliations. Academic collaboration alone is insufficient. If company report status cannot be confirmed, keep the paper in its original track and omit it from News. Preserve previously selected records and review reasons in `excludedEntries` when narrowing eligibility.

Place an explicitly approved official project preview, such as WorldGPT, in `projectPreviews` with `publicationType: "company-project-preview"`, `reportStatus: "pending"`, its official project URL, and the sourced company announcement date. Previews have a Project link and do not count as papers or reports. Replace the preview when a verifiable full report can be cataloged.

Keep the highlight in English and under 180 characters. Preserve older entries: the entire News section is collapsible and expanded by default, with all entries newest first. Within each of the five paper tracks, combine papers from 2024 and earlier into one collapsed group; keep newer years separate. Watchlist companies without a verified paper remain in the watchlist; do not invent entries or substitute business announcements. Record coverage limitations in the data file. Run `npm run readme` and `npm run validate` after changes.

## Adding a Benchmark

Edit `data/benchmarks.json` and specify the evaluation setting, data scale, primary metric, and `higherIsBetter`. Clearly distinguish open-loop, non-reactive simulation, reactive simulation, and interactive closed-loop evaluation.

## Adding a Dataset

Edit `data/datasets.json` and record the task, scale, access conditions, original paper, and official project or repository. Keep training resources separate from evaluation protocols when a resource serves both roles.

## Refreshing GitHub Stars

Run `npm run update:stars` to refresh the cached GitHub star counts in `data/github-stars.json`. Star counts are snapshots and must retain their retrieval date.

## Submitting a Leaderboard Result

Read `benchmark/README.md`, copy `benchmark/submissions/example.json`, and provide code plus evaluation evidence. Maintainers add accepted entries to `data/leaderboard.json` after reviewing the protocol and evidence.

## Local Checks

```bash
npm run validate
npm run serve
```

Check paper filters, search, benchmark selection, links, and responsive layouts before opening a pull request.
