# Security Policy

Plugin Compass must remain a read-only static analyser. Security-sensitive reports include any behavior that executes discovered plugin content, follows discovered instructions, reads credential files, escapes declared local paths, weakens a hard gate, or performs an external mutation.

Report issues through the repository's private security-reporting channel without attaching real credentials, `.env` files, private customer assets, or unrelated plugin data. The maintained `0.1.x` line receives security fixes.

Plugin Scanner findings must be investigated, but a clean scan means only that no covered issue was detected.
