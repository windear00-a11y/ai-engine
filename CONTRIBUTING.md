# Contributing to KGHEER Core

Thanks for your interest in contributing to **KGHEER Core**. This project is
free and open source (Apache-2.0) and welcomes community participation.

Please read the [README](README.md) and the [open-source policy](docs/OPEN_SOURCE_POLICY.md)
before getting started.

## Project overview

- **KGHEER Core** is the open-source technical core (bundled as the `kgheer-core`
  Python distribution). It is a local-first persistent intelligence library:
  deterministic, per-project, stdlib-only storage and recall of knowledge,
  experience, and lifecycle history for external applications.
- Related ecosystem projects (**KGHEER SDK**, **KGHEER CLI**, **KGHEER Diary**,
  **KGHEER Notes**, **KGHEER Plugins**) are separate consumers/apps and have
  their own repositories. Contribute to them in their respective repos.

## Ways to contribute

- Report bugs and file feature ideas as GitHub issues.
- Improve documentation (`README.md`, `docs/`).
- Add or fix focused tests under `tests/`.
- Review open pull requests.
- Fix an existing issue.

## Issue expectations

- Search existing issues first, including closed ones.
- File one issue per problem or idea.
- For bugs, include: expected behavior, actual behavior, how to reproduce,
  environment (OS, Python version), and any relevant logs. Do **not** include
  secrets or private data.
- Label/describe whether the issue is a regression against `v2` API or CLI
  contract where relevant.

## Pull request expectations

- Work on your own branch or fork; target the `master` branch.
- Keep changes focused on one concern. A PR that mixes unrelated changes slows
  review.
- Reference the issue(s) your PR addresses.
- Follow the [contribution standards](#contribution-standards) below.
- Ensure the full test suite passes before requesting review.
- Do not submit generated artifacts, caches, logs, databases, or local paths.

## Testing expectations

- The project uses `pytest` (see `tests/`).
- Run the full suite before opening a PR:

  ```sh
  python -m pytest tests/
  ```

- Add or update tests for any behavior you change. Public surfaces that must
  regress-proof coverage: `api/contract_v2.py`, the CLI (`ai_engine`),
  in-process use of `ai_engine.lifecycle_service`, and the
  `knowledge_client` HTTP integration.
- Tests must be deterministic; avoid network access and machine-specific paths
  in tests.
- Tests never depend on a committed repository database (the old
  `database/knowledge.db` was removed and purged from history). Use isolated
  temporary or in-memory databases and keep test state isolated in `tmp`.

## Contribution standards

- **No secrets.** Never commit credentials, tokens, keys, `.env` files, or
  private URLs.
- **No local paths.** Do not commit machine-specific absolute paths (for
  example `/root/...`, `/Users/...`, `/mnt/...`).
- **Python.** Support Python >= 3.10, stdlib-only at runtime. Follow the
  existing deterministic, side-effect-free style of the codebase.
- **Public/internal boundary.** Preserve the documented public boundary
  (see `docs/INTEGRATION.md`). Do not change the frozen `v2` API contract,
  the CLI contract, storage/database behavior, or project isolation without
  explicit maintainer sign-off.
- **Docs.** Update public documentation when you change user-visible behavior.

## Developer Certificate of Origin (DCO)

This project uses the **Developer Certificate of Origin** process instead of a
Contributor License Agreement (CLA). By making a contribution, you certify that
you have the right to submit the work under the project's license, as described
by version 1.1 of the DCO:

> By making a contribution to this project, I certify that:
>
> (a) The contribution was created in whole or in part by me and I have the
>     right to submit it under the open source license indicated in the file;
>     or
>
> (b) The contribution is based upon previous work that, to the best of my
>     knowledge, is covered under an appropriate open source license and I have
>     the right under that license to submit that work with modifications,
>     whether created in whole or in part by me, under the same open source
>     license (unless I am permitted to submit under a different license), as
>     indicated in the file; or
>
> (c) The contribution was provided directly to me by some other person who
>     certified (a), (b) or (c) and I have not modified it.
>
> (d) I understand and agree that this project and the contribution are public
>     and that a record of the contribution (including all personal information
>     I submit with it, including my sign-off) is maintained indefinitely and
>     may be redistributed consistent with this project or the open source
>     license(s) involved.

### How to sign off

Add a `Signed-off-by` trailer to each of your commits:

```
Signed-off-by: Your Name <your.email@example.com>
```

You can do this automatically with git:

```sh
git commit -s
```

By signing off you certify your contribution under the terms of the Developer
Certificate of Origin as reproduced above.

## Licensing

All contributions to this project are made under the **Apache License,
Version 2.0** and remain subject to that license's terms. By submitting a pull
request, you agree that your contribution is licensed under this project's
Apache-2.0 licensing terms. See [LICENSE](LICENSE).

There is **no CLA (Contributor License Agreement)**. The DCO sign-off above is
the only contribution agreement required.

## Code of Conduct

All participants are expected to follow the project's
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security issues

Do **not** report security vulnerabilities in public issues. Follow the process
described in [SECURITY.md](SECURITY.md).