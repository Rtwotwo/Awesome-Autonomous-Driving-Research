# User guide

## Import and identify

Use an arXiv abstract/PDF URL, ID, or local PDF. Verify the original English title, authors, affiliations, paper URL, project page, and code repository. PDF uploads can identify an embedded arXiv ID and fetch metadata. If that service fails, use **Re-identify paper information** or edit the card. Preserve manually verified values.

Rename a workspace item without changing its paper title; pin frequent items or move them to the reversible recycle bin. Active items cannot be removed while processing.

## Connect a model

Create a named profile with base URL, exact model ID, and API key. OpenAI-compatible does not guarantee image support or identical context limits. Save credentials only for Windows-user-encrypted persistence; otherwise use memory-only mode. Keep keys out of prompts, screenshots, exports, and Git. Changing providers requires checking the key again.

Vision analysis needs a compatible model. Text-only models can use extracted text and captions. Provider usage can incur charges. Local storage does not prevent selected content from being sent to the configured model.

## Prepare illustrations

Check architecture diagrams, quantitative results, and qualitative examples. Embedded images may be only one panel, with vector labels or arrows outside the raster object. Prefer a complete caption-based crop when needed. Use native extraction for complete raster figures or import a clean PNG/JPEG replacement.

Uncertain full-page candidates stay unselected. Crop them manually, checking axes, table headings, units, legends, and all panels. Retain attribution for project-page media. Importing an image does not grant permission to redistribute it.

## Write and design

Select a technical explanation, overview, or available editorial style. Adaptive mode chooses structure from the paper and available evidence. A length target is not permission to invent content. Long responses use bounded continuation and private stage checkpoints.

Review the identity card, mechanism, experimental conditions, comparisons, qualitative examples, and limitations. Page arrays belong in the evidence panel, not the final prose. Apply a built-in theme or Baoyu formatting; generate a wide or square local cover if useful. Inspect the mobile preview. HTML/ZIP export creates local files only.

## Submit a draft

1. Enter AppID/AppSecret locally. Confirm material and draft permissions in the official WeChat developer console.
2. Allowlist the actual public egress IP. VPNs, proxies, and network changes can change it.
3. Open stage 04, **Automatic checks and delivery**. Choose **Prepare and check only** for a local preview, or **Automatically prepare and deliver** for the complete draft-box workflow.
4. Missing upload fields are prepared automatically. The default byline is Paper Studio. An eligible overview/architecture figure is fitted into a wide cover without cutting away labels. Existing custom fields and cover selections remain intact.
5. Inspect the result and receipt. Expand the editing panel to customize title, byline, digest, cover or layout. The optional human-review panel retains the previous checkbox/approval workflow, but is not required for automatic delivery.
6. Check the official draft box, add videos where needed, send a phone preview, and publish there.

The upload adapter conservatively uses a 32-character title, 16-character byline, and 120-character card digest. These are application policies, not manuscript limits. The short digest is separate from the body summary. HTML markup counts toward the body budget: the application can compact it, but does not silently delete article content.

Automatic checks cover article structure, referenced assets, cover availability,
upload limits and target-account readiness. Evidence-matching and coverage
warnings remain visible; they do not become a claim that a person reviewed the
paper. Paper Studio is the default editor, not a fabricated human sign-off. An
unreviewed imported image or uncertain whole-page crop may still require a
manual correction before upload. Previously submitted or ambiguous deliveries
must be checked through their receipt before any resend.

Use the writing panel's generation-and-delivery option to chain a completed
generation into the same automatic workflow. When an account is configured,
this clearly labelled option starts checked on application launch; uncheck it
for local-only writing during the current session. Once started, the server
keeps the selected workflow running while you browse other papers. Opening a
paper or changing a setting does not itself submit it. A failed or cancelled
generation does not deliver a partial article. Restarting the application does
not automatically retry an interrupted delivery. Existing arXiv direct-delivery and serial queues remain
available; this feature does not turn on a paused daily schedule.

Automatic covers are refreshed when their title or source figure changes;
explicit cover choices are preserved. A valid saved Baoyu layout is reused.
If it is absent or stale and the formatter cannot run, the workflow selects the
built-in ink theme and records the fallback in the check report.

## Direct and batch modes

Direct mode explicitly selects a paper for automatic generation and draft submission. Discovery mode additionally checks project and implementation resources. Neither mode claims human approval; review the result in WeChat.

Metadata is listed first. Each queue downloads and completes one paper before starting the next. Immediate stop interrupts at the next safe boundary; finish-current stops before the next paper. A stop cannot undo a request already accepted by WeChat. Check receipts before retrying.

Configure topics, model, target account, and daily cap before enabling an external scheduler. The default is five attempts per Beijing day, counting processing attempts even when they later fail, to avoid uncontrolled retries. No eligible paper means no article. Installation creates no scheduled task.

## Export and recovery

ZIP bundles contain article HTML/assets and structured editing/evidence files. They cannot be uploaded directly as a WeChat article. Use API delivery or copy rendered HTML and upload local media manually.

Back up runtime data separately from source. DPAPI settings normally require the same Windows user and machine environment; re-enter credentials on a new computer. Never attach a private backup to a public issue.
