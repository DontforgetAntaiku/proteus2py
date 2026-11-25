# Security

proteus2py drives a desktop program (Proteus 8 under Wine) and runs it in a bubblewrap sandbox: no network, an empty `$HOME`,
and only the Wine prefix copy and the exchange folder writable. Your files are copied in, never modified.
If you find a way to escape the sandbox or to make the tools touch files outside the exchange folder, report it privately via
GitHub's "Report a vulnerability" (Security tab) instead of a public issue.
