# Installation and troubleshooting

For a generation failure, inspect `generation-error.json` and `job-error.json`
in the private task folder. They record stage, exception type/code, timing, and
code locations, without raw provider responses, keys, headers, prompts, or
reasoning text. `generation-partial.json` contains private returned article text;
do not attach it to a public issue without reviewing its contents.

For an editorial-review structure error, the writer automatically retries the
current unit with schema feedback up to twice. It does not discard earlier
reviewed units or mark a partial article ready for delivery. `0`/`null` in an
empty figure/table slot is handled locally; malformed nonempty attachments,
missing paragraphs and changed image IDs still require a valid replacement.
Use **Continue and deliver draft** after a remaining provider failure. Do not
start a fresh regeneration unless you intend to replace the checkpoint.

A zero figure count does not necessarily mean the PDF has no figures. Automatic
illustration retries older zero-figure jobs, including pipe-delimited captions.
Uncertain full-page crops are not promoted into confirmed crops. Native image
replacement and checkpoint migration preserve old referenced images on cancel
or write failure; unused new files may remain in the private task folder.

`IncompleteRead`, `RemoteDisconnected`, and connection-reset messages indicate
transport interruption, not lack of vision. Streaming preserves received text
and retries the current request up to twice. Failed-attempt text is not joined to
the replacement response. Cancellation is checked between received stream lines;
an unresponsive connection remains bounded by the configured socket timeout.
If a provider rejects streaming, select **Standard JSON** in model settings. If
it rejects `response_format`, leave JSON mode at **Auto** or **Disabled**. HTTP
400/422 also requires checking the provider's allowed `max_tokens`; Paper Studio
does not silently rename your model or lower your configured output budget.

A standard JSON completion needs a recognized `finish_reason`; a missing/null
marker is reported as an interface error. Streaming needs a finish marker or
`[DONE]`. Content-Length truncation and unfinished streams use bounded retries.
Local decoding guards are separate from token budgets: 8 MB of streamed text
(including reasoning, which is not saved), 512 MB of stream framing, and 2 MB per
event. Standard JSON envelopes and compatibility responses without an SSE
content type remain capped at 8 MB; the existing accumulated article guard is
4 million characters. These bounds are not provider output limits.

| Symptom | Action |
|---|---|
| `py` is missing | Install Python 3.11 with its launcher, or create `.venv` using your Python 3.11 executable and rerun setup. |
| PowerShell blocks a script | Use the documented per-invocation `-ExecutionPolicy Bypass` command; do not change machine-wide policy. |
| Package download fails | Check network and package-index settings, then rerun setup. Keep TLS verification enabled. |
| Blank desktop window | Install/repair Microsoft WebView2 Runtime. Run `python.exe desktop.py` from a terminal for local diagnostics. |
| Already-running notice | Relaunch should activate the existing window. Save work before terminating a genuinely stuck process. |
| No articles in the checkout | Data is outside source: `%LOCALAPPDATA%\PaperStudio`, your override, or a legacy private installation directory. |
| Incorrect PDF title | Re-identify the paper and check its information card; unusual page layouts still need manual verification. |
| PDF has no usable text | Run OCR externally and import the searchable result. Page images are not extracted evidence. |
| Incomplete or loose crops | Manually crop or replace with a complete image. Native embedded images may omit vector labels. |
| Missing result illustrations | Check selection and coverage for architecture, quantitative, and qualitative figures. Do not use unrelated images to satisfy coverage. |
| Model output ends early | Verify exact model ID and provider output cap; resume the saved stage. Bounded continuation cannot overcome context limits or every malformed response. |
| Generation times out | Completed stages remain available. Inspect the current job before retrying; avoid concurrent duplicate jobs. |
| Baoyu unavailable | Run `setup-formatter.ps1`, ensure Node.js/npm is available, and permit first-use package downloads. Built-in layouts work offline. |
| WeChat error `40164` | Add the API request's actual public IP to the account allowlist and save. Check proxy/VPN routing; private LAN IPs are not substitutes. |
| Authentication/permission error | Verify AppID/AppSecret pairing and actual material/draft permissions. AppID presence alone is insufficient. |
| Approval was invalidated | Content, image, cover, layout, upload-field, or account changes require reviewing the new snapshot. |
| Stage 04 still shows human checkboxes | The human-review panel is optional. Use the separate automatic preparation/delivery action; it does not require a human approval record. |
| Automated checks show evidence warnings | Read the listed issues in the draft box. Warnings are not scientific certification; unusable assets or payload errors still block upload. |
| Overview cover uses a fallback | Check original figure captions and crop quality. Without an eligible overview, the software identifies a safe figure or typographic fallback instead of inventing a diagram. |
| Digest exceeds 120 characters | Use field preparation; it shortens the upload card while preserving original content and adjustment history. |
| HTML body exceeds budget | Compact formatting first, then explicitly edit large/repeated content if necessary and review again. |
| Submission outcome unknown | Inspect WeChat and the local receipt before retrying. Blind retries may create duplicates. |
| Videos absent in draft | Insert them in the official editor. Local MP4 files do not upload with HTML. |
| Daily run produces nothing | Check enablement, external scheduling, eligibility, resource verification, daily cap, and whether the computer is awake. No eligible papers is a normal no-op. |
| Encrypted credentials fail after migration | Re-enter on the new computer. Do not turn encrypted configuration into plaintext files. |

For reproduction, use synthetic or public papers and include OS/Python versions plus a redacted error. Never attach `.dpapi` files, private runtime folders, keys, cookies, token-bearing API responses, or personal account screenshots.
