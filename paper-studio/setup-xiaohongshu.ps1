param([string]$HomeDirectory = '')
$ErrorActionPreference = 'Stop'
# Security hold: do not download, execute, restore, or allowlist the affected
# connector. Keep this entry point so old installation instructions fail closed.
throw 'Xiaohongshu connector installation is paused: its v2.5.5 browser helper triggered a Windows Defender detection. Do not allowlist or restore it. Local note design and ZIP export remain available; upload the exported materials through https://creator.xiaohongshu.com/ instead.'
