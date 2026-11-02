# Xiaohongshu image notes

## Current status: local design and export only

Automatic login and posting are paused as of **2026-10-23**. The previously pinned
`xiaohongshu-mcp v2.5.5` connector releases a `leakless.exe` browser-lifecycle helper.
Windows Defender detected that helper on the tested installation as
`Trojan:Win32/Kepavll!rfn`, preventing the QR-login path from completing. The
connector executable matched the pinned upstream SHA-256 and was unsigned.
Neither that hash match nor the helper's documented purpose proves a false positive.

The app blocks startup, QR login, account checks, and automated posting for this
version. The setup script also stops before downloading or changing any files.
Existing local drafts, image notes, receipts, and account session files are retained.
**Do not restore or allowlist the helper, add antivirus exclusions, or disable
Defender.** Leave detection handling to Windows Security. Do not repeatedly click
login in an older app build.

The previous integration passed local rendering, transport simulations and a
companion health check, but it had not passed a real browser-launch/QR-login test.
A replacement must have a reviewed launch chain and pass that test with antivirus
protection enabled. There is no validated replacement in this release.

## Design and post through the official platform

1. Save the original research article and select complete, legible paper figures.
2. Open **05 Xiaohongshu** and click **Prepare note**. This creates a separate short
   note and portrait cards from the saved article and selected figures.
3. Edit the title, caption and topics; select and reorder cards. Saving a short
   title updates the cover. The full original article remains unchanged.
4. Click **Download image-note ZIP**, extract it, and inspect the images and text.
5. Open the [official creator platform](https://creator.xiaohongshu.com/), log in
   yourself, upload the selected images, paste the title/caption, and check the
   intended account and visibility before publishing.

The exported ZIP is a material bundle, not a format accepted directly as a post.
Local preparation does not call a model or publish anything. It excerpts saved
evidence-grounded text and fits original figures without cropping axes or legends;
it cannot repair a source image that was already incomplete. Text-only writing
models remain compatible. Videos are not included in this image-note channel.

## Limits and existing receipts

- The local template supports a 20-character title, 1,000-character caption with
  topics, and up to nine images. These are adapter limits, not universal platform
  limits. Manually entered text is validated rather than silently truncated.
- A text-only warning means no selected figure passed the figure checks. Replace
  incomplete crops in stage 02 and regenerate the note.
- Existing **Submitted, verification required** or **Unknown outcome** receipts
  do not establish that a post is absent or published. Check the official account
  before any manual repost to avoid duplicates.
- Local sessions remain under `connectors/xiaohongshu` in the private application
  directory. Cookies are plaintext credentials. Do not share this directory or
  include it in source control, screenshots or support bundles.

## Third-party boundary

The paused companion is [xpzouying/xiaohongshu-mcp v2.5.5](https://github.com/xpzouying/xiaohongshu-mcp/releases/tag/v2.5.5)
(Apache-2.0), an independent browser automation project. It is not an official
Xiaohongshu OAuth client or publishing API. Its service health endpoint can respond
before its browser is ever launched; health alone is not a login readiness check.
