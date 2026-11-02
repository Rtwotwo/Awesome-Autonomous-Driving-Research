# Privacy and security

Paper Studio is a single-user local desktop application. Its loopback service is not intended for public hosting, reverse-proxy exposure, or untrusted multi-user access.

Optional secret persistence uses Windows DPAPI for the current user, outside the checkout. Memory-only mode is available. Encryption does not protect against malware running as that user. Protect backups and runtime data accordingly.

Per-model authentication and proxy controls are private settings, not source
presets. URL query parameters accept routing/version flags only; credentials
must use the protected key field. No authenticated redirect is followed, and
TLS verification is never disabled. Direct mode changes only this model's
connection, not system-wide proxy settings. Safe diagnostics report error
categories rather than exception strings, headers, URLs or response bodies.

Network requests have explicit purposes: arXiv/project discovery fetches public resources; writing sends selected text and optional images to the configured model; draft submission uploads prepared content to WeChat; optional formatter setup downloads packages. Local storage does not imply offline inference.

The optional Xiaohongshu companion is on security hold as of 2026-10-23.
Its browser helper triggered Windows Defender; automatic login and posting are
disabled pending a validated replacement. Do not allowlist or restore the flagged
helper, or disable endpoint protection. A matching upstream checksum establishes
artifact identity, not that it is safe or that a detection is a false positive.
The transport described below is retained for future replacement work. It runs on loopback behind a per-run backend token; the UI
does not receive that token. Its upstream login cookies are plaintext files in the
private application directory, not the DPAPI model store. Exclude the entire
`connectors` directory from repositories and shared backups. Never export it as a
support bundle. Closing the app terminates its managed companion and browser
processes but preserves the private cookie file for subsequent login reuse.

The public source contains no account configuration or private documents. Git ignore rules are a convenience, not a guarantee: inspect staged paths and content before committing. Do not force-add runtime files. GitHub Pages deploys only the static research catalog.

Report issues with synthetic inputs and redacted diagnostics. Never post a real model key, AppSecret, access token, cookie, private manuscript, or runtime archive. If a credential is disclosed, revoke it through its provider; deleting a visible file does not remove copies from Git history.
