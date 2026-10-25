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
