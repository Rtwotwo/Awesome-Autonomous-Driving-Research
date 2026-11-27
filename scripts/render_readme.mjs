import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..');
const readJson = async (name) => JSON.parse(await readFile(join(root, 'data', name), 'utf8'));
const [papers, datasets, benchmarks, industryNews] = await Promise.all([
  readJson('papers.json'), readJson('datasets.json'), readJson('benchmarks.json'), readJson('industry-news.json')
]);

const tracks = [
  ['e2e', 'End-to-End Autonomous Driving'],
  ['vla', 'Vision-Language-Action Models'],
  ['world-model', 'Driving World Models'],
  ['rsi', 'Recursive Self-Improvement for Autonomous Driving'],
  ['perception-other', 'Perception & Other Driving Research']
];
const datasetCategories = [
  'Perception and Multimodal Understanding',
  'Motion, Planning, and Cooperative Driving',
  'Simulation and Synthetic Data'
];
const benchmarkCategories = [
  'Closed-Loop Planning',
  'Open-Loop and Non-Reactive Planning',
  'VLA and Driving Reasoning',
  'Reliability and Robustness'
];
const anchor = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const iconText = {
  method: '\u{1F9E0}', year: '\u{1F5D3}\uFE0F', tags: '\u{1F3F7}\uFE0F',
  paper: '\u{1F4C4}', github: '\u{1F4BB}', project: '\u{1F310}',
  dataset: '\u{1F30D}'
};
const dash = '\u2014';
const esc = (value) => String(value ?? '').replaceAll('|', '\\|');
const html = (value) => esc(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const icon = (emoji, url, title) => url ? `[${emoji}](${url} "${title}")` : dash;
const attr = (value) => html(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const resourceIcon = (name, url, title) => url
  ? `<a href="${attr(url)}" title="${attr(title)}"><img src="assets/icons/${name}.svg" alt="${attr(title)}" width="28" height="24"></a>`
  : '';
const paperResources = (paper) => {
  const repo = paper.githubRepo || paper.code;
  const state = paper.codeStatus === 'pending' ? 'pending'
    : paper.codeScope === 'partial' ? 'partial'
    : paper.codeStatus === 'released' ? 'released' : 'project-only';
  const glyph = state === 'pending' ? 'github-pending'
    : state === 'partial' ? 'github-partial' : 'github';
  const description = {
    pending: 'GitHub — code pending', partial: 'GitHub — partial code release',
    released: 'GitHub — code released', 'project-only': 'GitHub — project repository; no verified implementation'
  }[state];
  const canonical = (url) => (url || '').replace(/\/$/, '').toLowerCase();
  const project = paper.project && canonical(paper.project) !== canonical(repo) ? paper.project : null;
  return [resourceIcon('paper', paper.paper, 'Read paper'),
    resourceIcon(glyph, repo, description), resourceIcon('project', project, 'Official project page')]
    .filter(Boolean).join('&nbsp;');
};
const resources = (item) => [
  icon(iconText.dataset, item.homepage, 'Dataset homepage'),
  icon(iconText.paper, item.paper, 'Paper'),
  icon(iconText.github, item.code, 'Dataset, download, or code')
].filter((value) => value !== dash).join(' ');

const reviewDate = process.env.CATALOG_REVIEW_DATE || new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
const trackAnchor = (title) => title.toLowerCase().replace(/&/g, '').replace(/ /g, '-').replace(/[^a-z0-9-]/g, '');
let readme = '<p align="center"><img src="assets/branding/autodrive-research-logo.png" alt="AutoDrive Research — Awesome Autonomous Driving Research" width="360"></p>\n\n';
readme += '<p align="center"><strong>A research map for autonomous driving.</strong><br><sub>Explore papers, implementations, datasets, and evaluation benchmarks.</sub></p>\n\n';
readme += `<p align="center"><strong>${papers.length}</strong> papers &nbsp;·&nbsp; <strong>${datasets.length}</strong> datasets &nbsp;·&nbsp; <strong>${benchmarks.length}</strong> benchmarks<br><sub>Updated ${reviewDate} · Verified paper &amp; author links</sub></p>\n\n`;
readme += '<p align="center"><a href="#industry-research-news">News</a> &nbsp;/&nbsp; <a href="#papers">Papers</a> &nbsp;/&nbsp; <a href="#public-datasets">Datasets</a> &nbsp;/&nbsp; <a href="#public-evaluation-benchmarks">Benchmarks</a> &nbsp;/&nbsp; <a href="#leaderboard">Leaderboard</a> &nbsp;/&nbsp; <a href="CONTRIBUTING.md">Contribute</a></p>\n\n';
readme += '> **Community** · [Join our WeChat group](assets/branding/wechat-group-20261124.png) for paper discussions and research exchange.\n>\n> <sub>Open the invitation image and scan it in WeChat. Valid before December 1, 2026.</sub>\n\n';
readme += '## Industry Research News\n\n';
const projectPreviews = industryNews.projectPreviews ?? [];
const previewCount = projectPreviews.length ? ` · ${projectPreviews.length} project preview${projectPreviews.length === 1 ? '' : 's'}` : '';
readme += `<details open>\n<summary><strong>Company research</strong> · ${industryNews.entries.length} reports${previewCount}</summary>\n\n`;
readme += '**On our radar** · ' + industryNews.watchlist.map(html).join(' · ') + '\n\n';
readme += `<sub>Since ${html(industryNews.coverageStart)} · Official company-team reports link to a research track. Dates use the first arXiv submission or the labeled official release date.</sub>\n\n`;
const newsPapers = new Map(papers.map(p => [p.id, p]));
const newsItems = [...industryNews.entries, ...projectPreviews]
  .sort((a, b) => b.date.localeCompare(a.date) || (a.paperId ?? a.name).localeCompare(b.paperId ?? b.name));
const newsTable = (items) => {
  let table = '| Date | Company | Research highlight | Links |\n|:---|:---|:---|:---:|\n';
  for (const item of items) {
    const companies = item.companies.map(html).join(' · ');
    if (item.publicationType === 'company-project-preview') {
      table += `| ${item.date}<br><sub>Announcement</sub> | **${companies}** | **${html(item.name)}**<br><sub>Project preview · Report pending · ${html(item.highlight)}</sub> | ${resourceIcon('project', item.project, 'Official project preview')} |\n`;
      continue;
    }
    const paper = newsPapers.get(item.paperId);
    if (!paper) throw new Error(`Industry news references unknown paper: ${item.paperId}`);
    const trackTitle = tracks.find(([id]) => id === paper.track)?.[1];
    if (!trackTitle) throw new Error(`Industry news references unknown track: ${paper.track}`);
    const dateLabel = item.dateType === 'official-report-release' ? '<br><sub>Report release</sub>' : '';
    const predecessors = (item.predecessorPaperIds ?? []).map(id => {
      const predecessor = newsPapers.get(id);
      return `[${html(predecessor.name)} (${html(predecessor.submitted ?? predecessor.year)})](${predecessor.paper})`;
    });
    const series = predecessors.length ? `<br><sub>Earlier in the series · ${predecessors.join(' · ')}</sub>` : '';
    table += `| ${item.date}${dateLabel} | **${companies}** | [**${html(paper.name)}**](#${trackAnchor(trackTitle)})<br><sub>${html(item.highlight)}</sub>${series} | ${paperResources(paper)} |\n`;
  }
  return table + '\n';
};
readme += newsTable(newsItems);
readme += '<sub>[Sources & coverage](data/industry-news.json) · Watchlist membership does not imply a verified paper entry.</sub>\n\n';
readme += '</details>\n\n';
readme += '## Research Tracks\n\n';
const trackLabels = ['E2E','VLA','World Models','RSI','Perception'];
readme += '| ' + tracks.map(([,title],i)=>`[**${trackLabels[i]}**](#${trackAnchor(title)})`).join(' | ') + ' |\n';
readme += '|:---:|:---:|:---:|:---:|:---:|\n';
readme += '| ' + tracks.map(([track])=>`${papers.filter(p=>p.track===track).length} papers`).join(' | ') + ' |\n\n';
readme += '<details>\n<summary><strong>Resource Guide</strong> · Datasets, evaluation protocols, and tools</summary>\n\n';
readme += 'E2E: end-to-end driving. VLA: vision-language-action models. RSI: explicit iterative self-improvement loops. Perception includes supporting driving research.\n\n';
readme += '**Datasets** · ' + datasetCategories.map(title=>`[${title}](#${anchor(title)})`).join(' · ') + '\n\n';
readme += '**Benchmarks** · ' + benchmarkCategories.map(title=>`[${title}](#${anchor(title)})`).join(' · ') + '\n\n';
readme += '[Paper Studio](paper-studio/README.md) · Local paper-to-WeChat workspace ([noncommercial source license](paper-studio/LICENSE)).\n\n';
readme += '[Link audit](data/github-audit.json) · [Discovery history](data/adfeed-audit.json) · [Media discoveries](data/media-discovery-audit.json) · [License](#license)\n\n</details>\n\n';
readme += '## Papers\n\n';
readme += '![Paper](assets/icons/paper.svg) Paper · ![GitHub](assets/icons/github.svg) GitHub · ![Project](assets/icons/project.svg) Project · ![Pending](assets/icons/github-pending.svg) Pending · ![Partial](assets/icons/github-partial.svg) Partial. Missing links are omitted.\n\n';
readme += '<sub>Newest first · 2024 and earlier grouped together · Dataset names verified against papers; unknowns left blank.</sub>\n\n';

for (const [track, title] of tracks) {
  const trackPapers = papers.filter((item) => item.track === track);
  const codeCount = trackPapers.filter((paper) => paper.openSource && paper.code).length;
  readme += `### ${title}\n\n`;
  readme += `_${trackPapers.length} papers; ${codeCount} with a verified public code link._\n\n`;
  const years = [...new Set(trackPapers.filter(p => p.year > 2024).map(p => p.year))].sort((a, b) => b - a);
  const groups = years.map(year => ({ label: String(year), items: trackPapers.filter(p => p.year === year), open: year === years[0] }));
  const earlier = trackPapers.filter(p => p.year <= 2024);
  if (earlier.length) groups.push({ label: '2024 and earlier', items: earlier, open: false });
  for (const group of groups) {
    readme += `<details${group.open ? ' open' : ''}>\n<summary><strong>${group.label}</strong> · ${group.items.length} papers</summary>\n\n`;
    readme += '| **Method / Paper** | **Venue** | **Focus** | **Datasets** | **Links** |\n';
    readme += '|---|---|---|---|:---:|\n';
    for (const paper of group.items) {
      const yearVenue = paper.venue === String(paper.year) ? String(paper.year) : esc(paper.venue);
      const methodTitle = `**${esc(paper.name)}**` + (paper.name === paper.title ? '' : `<br><sub>${html(paper.title)}</sub>`);
      const tags = `<sub>${paper.tags.map(html).join(' · ')}</sub>`;
      const datasetNames = (paper.datasets ?? []).map(html).join(', ');
      readme += `| ${methodTitle} | ${yearVenue} | ${tags} | ${datasetNames} | ${paperResources(paper)} |\n`;
    }
    readme += '\n</details>\n\n';
  }
}

readme += '## Public Datasets\n\n';
readme += 'Dataset access terms and releases follow the original providers. The source and inspiration repositories are listed in Acknowledgements.\n\n';
for (const category of datasetCategories) {
  const items = datasets.filter((item) => item.category === category);
  if (!items.length) continue;
  readme += `### ${category}\n\n_${items.length} datasets._\n\n`;
  readme += '| Dataset / Method and Paper Title | Task | Scale | Access | Resources |\n';
  readme += '|---|---|---|---|:---:|\n';
  for (const item of items) {
    const nameTitle = `**${esc(item.name)}**<br><sub>${html(item.paperTitle)}</sub>`;
    readme += `| ${nameTitle} | ${esc(item.task)} | ${esc(item.scale)} | ${esc(item.access)} | ${resources(item)} |\n`;
  }
  readme += '\n';
}

readme += '## Public Evaluation Benchmarks\n\n';
readme += 'Benchmarks are grouped by evaluation protocol and research task; scores from different protocols remain separate.\n\n';
for (const category of benchmarkCategories) {
  const items = benchmarks.filter((item) => item.category === category);
  if (!items.length) continue;
  readme += `### ${category}\n\n_${items.length} benchmark${items.length === 1 ? '' : 's'}._\n\n`;
  readme += '| Benchmark | Track | Evaluation Setting | Primary Metric | Resources |\n|---|---|---|---|:---:|\n';
  for (const item of items) {
    const links = [
      icon(iconText.dataset, item.homepage, 'Benchmark homepage'),
      icon(iconText.paper, item.paper, 'Paper'),
      icon(iconText.github, item.code, 'Code')
    ].filter((value) => value !== dash).join(' ');
    readme += `| **${esc(item.name)}** | ${esc(item.track)} | ${esc(item.setting)} | ${esc(item.primaryMetric)} | ${links} |\n`;
  }
  readme += '\n';
}

readme += '\n## Leaderboard\n\n';
readme += 'Benchmark results are stored in [data/leaderboard.json](data/leaderboard.json). Each entry preserves its evaluation protocol, data split, and reproducibility evidence. Results from incompatible protocols are not ranked together.\n\n';
readme += '## License\n\nThe AutoDrive catalog and website use the [Apache License 2.0](LICENSE). The companion [Paper Studio application](paper-studio/README.md) uses [PolyForm Noncommercial 1.0.0](paper-studio/LICENSE); its original code is source-available for noncommercial use. Third-party components retain their own licenses.\n';
readme += '\n## Acknowledgements\n\n';
readme += 'We thank the maintainers of [GE2EAD](https://github.com/AutoLab-SAI-SJTU/GE2EAD), [GenAI4AD](https://github.com/taco-group/GenAI4AD), and [ReCogDrive](https://github.com/xiaomi-research/recogdrive) for their research collections and dataset references, which helped inform this catalog. We also acknowledge [ADFeed](https://mp.weixin.qq.com/s/Q1ez7hgkQ8gLLI1T-XvJlA) as an editorial discovery reference and [Awesome World Models](https://github.com/LMD0311/Awesome-World-Model) for discovery leads. Catalog facts and release status are verified against primary paper and author sources. Please consult the original projects for their own licenses and access terms.\n';

await writeFile(join(root, 'README.md'), readme, 'utf8');
console.log(`Rendered README for ${papers.length} papers, ${datasets.length} datasets, and ${benchmarks.length} benchmarks.`);
