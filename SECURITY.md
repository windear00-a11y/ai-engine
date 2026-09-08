# Security Policy

## Supported Versions

Security fixes are applied to the current release. Older releases are handled
on a best-effort basis. Because the `ai-engine` package ships with **no
runtime dependencies** and is locally executed, users are encouraged to stay on
the latest release.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :warning: best-effort |

## Reporting a Vulnerability

If you discover a security vulnerability, **do not** open a public issue.
Instead, report it privately to the maintainers (for example, via a private
security advisory or a closed channel), and include:

- A description of the vulnerability and its impact.
- Steps to reproduce (without including private data).
- Affected versions, if known.
- Any suggested remediation, if available.

You will receive an acknowledgement, and we will work to investigate and
address the report. Please allow a reasonable window before public
disclosure so the issue can be fixed.

## Scope

KGHEER Core is a local-first, stdlib-only library. Security-relevant areas
include:

- The HTTP server (`ai_engine serve`): request validation, body-size limits,
  and optional API-key authentication. Keep it bound to trusted networks
  (`127.0.0.1` by default).
- Data stored under the data root (`AI_ENGINE_DATA_DIR`): treat it as trusted,
  locally accessed data. Permissions on the data root are the user's
  responsibility.
- Arbitrary code execution: inputs passed to scripts, tools, or importers are
  executed/processed locally; only use them with content you trust.

## Out of scope

- Misconfiguration of the operating environment (file permissions, network
  exposure, etc.) where the software's documented defaults are adhered to.
- Local data at rest beyond what the project explicitly encrypts (it does not).

## Best practices for consumers

- Keep the HTTP server bound to `127.0.0.1` unless you have specifically
  secured the network.
- Use a dedicated value for `--api-key` when exposing the server; never use a
  production credential or personal token.
- Back up your data root if you rely on it; uninstalling the package does not
  delete your data, but neither does it back it up.