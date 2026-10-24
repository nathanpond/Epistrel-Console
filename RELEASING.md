# Releasing the Epistrel Console

Images live at `ghcr.io/nathanpond/epistrel-console`, for `linux/amd64` and `linux/arm64`. Two channels:

| tag | what | when |
|---|---|---|
| `edge`, `sha-<7>` | whatever is on `main` right now; **unstable**, for running next to the Engine `edge` | every merge to `main` (`publish-edge.yml`) |
| `X.Y.Z`, `X.Y`, `X`, `latest` | a published release, tested against Engine `X.Y.Z` | a `vX.Y.Z` tag (`release.yml`, see [Cutting a release](#cutting-a-release)) |

Every publish runs the same gate as a pull request (`ci.yml`) first, then starts the built image and waits for its `/health` to answer, all before any tag moves. The image is pushed by digest, and `edge` (or the version tags) is added only after that smoke passes, so a failed run never moves a tag. `docker compose up` in this repo runs `edge`; `docker compose pull` picks up a newer one.

Publishing `edge` skips the live Engine smoke (`ci.yml` input `live_smoke: false`). The code already passed it on its PR, and an Engine outage must not freeze the Console's `edge`. Releases run it.

## Edge rollback

`edge` always tracks the newest merge. To put an earlier commit back on `edge`, dispatch the publish for that commit:

```bash
gh workflow run publish-edge.yml -f ref=<full 40-hex sha>
```

- The ref must be on `main`'s history. A branch that never merged is refused in the `resolve` job before anything is built. Short SHAs are rejected, and dispatches run only from `main`'s copy of the workflow.
- If that commit already has a `sha-<7>` image, it passed the gate when it was published. The run then re-points `edge` at that image's digest without rebuilding (the `retag` job) and checks the result. Otherwise it runs the full gate and build. The run summary says which path ran.
- Every `sha-<7>` tag stays addressable, so `ghcr.io/nathanpond/epistrel-console:sha-<7>` keeps working after `edge` moves.
- To restore, dispatch again with the current `main` SHA.

Watch a run with `gh run watch`.

## Cutting a release

The Console's version tracks the Engine's. Console `vX.Y.Z` is the Console for Engine `X.Y.Z`, and its release is tested against that Engine release's image instead of `edge`. There are no Console-only version numbers. A Console fix made after Console `X.Y.Z` reaches users through `edge` and waits for the Engine's next version. `-rc.N` is the only pre-release form, and Console `vX.Y.Z-rc.N` is tested against Engine `X.Y.Z-rc.N`, or `X.Y.Z` when that rc has no Engine image.

1. Open a PR that bumps `[project].version` in `pyproject.toml` (`X.Y.Z`, or `X.Y.ZrcN` for `vX.Y.Z-rc.N`). Merge it.
2. Tag the merge commit and push the tag:
   ```bash
   git switch main && git pull --ff-only
   git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z
   ```
   `/n8-release` in the n8SDLC workflow does the same. Only repository admins can create, move or delete `v*` tags, through the `release-tags` tag ruleset (`.github/rulesets/release-tags.json`, created with `gh api -X POST repos/nathanpond/Epistrel-Console/rulesets --input .github/rulesets/release-tags.json`).
3. `release.yml` runs these steps:
   - Checks that the tag is on `main`.
   - Runs `uv run python -m tools.release validate`, which refuses a malformed tag, a tag that doesn't match `pyproject.toml`, an rc of an already-released stable version, or a version with no matching Engine image.
   - Runs the full gate (`ci.yml`), including the live Engine smoke against that Engine image.
   - Pushes the multi-arch image by digest and checks that it answers `/health`, then adds the version tags.
   - Creates the GitHub release with notes generated from merged PR titles (categories in `.github/release.yml`). The notes end with the image line and the Engine it was tested with:
     ```text
     Image: `ghcr.io/nathanpond/epistrel-console:X.Y.Z@sha256:…`
     Tested with Engine <engine version> (`ghcr.io/nathanpond/epistrel@sha256:…`)
     ```

What results:

| tag pushed | needs Engine image | Console image tags | GitHub release |
|---|---|---|---|
| `v1.2.3`, highest stable so far | `1.2.3` | `1.2.3`, `1.2`, `1`, `latest` | marked **Latest** |
| `v1.2.3` while `1.3.0` exists | `1.2.3` | `1.2.3`, `1.2` (if highest in 1.2.x) | not marked latest |
| `v1.2.3-rc.1` | `1.2.3-rc.1`, else `1.2.3` | `1.2.3-rc.1` only | marked **Pre-release** |
| any, with no matching Engine image | — | nothing | none: `validate` fails |

`latest` is never moved by a pre-release, and never by a version lower than the highest stable version already published. Re-running the workflow for an existing tag rebuilds and re-pushes the version's image, then rewrites only the release's `Image:` and `Tested with Engine` lines (naming the newly tested Engine) and leaves the generated notes alone.

## Production rollback

A bad release is undone without rebuilding. `rollback.yml` re-tags the known-good multi-arch image as `latest` and demotes the bad GitHub release:

```bash
gh workflow run rollback.yml -f good_version=0.1.0 -f bad_version=0.1.1
```

Afterwards:

- `docker buildx imagetools inspect ghcr.io/nathanpond/epistrel-console:latest` reports `0.1.0`'s digest. So do `0.1` and `0`, if they pointed at the bad digest and `0.1.0` belongs to their line. Both architectures move together.
- The Releases page shows `v0.1.0` badged **Latest** and `v0.1.1` badged **Pre-release**, with `Rolled back on <date>; use v0.1.0.` appended to its notes. A re-run replaces that line rather than adding a second.
- The run's summary lists every tag with its before and after digest.

The run refuses, changing nothing, when any of these hold:

- an input is not a bare `X.Y.Z` (pre-releases can't be a `good_version`);
- `good_version == bad_version`;
- the good version's image tag or GitHub release doesn't exist;
- the bad version's release doesn't exist (when one is given);
- a `release.yml` run isn't finished (in progress, queued, waiting, pending or requested).

It runs only from `main`, and it shares the `release-latest` concurrency group with `release.yml`'s publish job, so a release and a rollback never interleave. `good_version` may be a release that was demoted earlier (it is promoted again) or one newer than `bad_version` (a roll forward).

Anyone who already pulled `latest` keeps the bad image until they pull again.

To remove a bad release entirely instead, delete its GitHub release and its version tag by hand (`gh release delete vX.Y.Z --cleanup-tag`, as an admin; the `release-tags` ruleset otherwise refuses tag deletion), then run the rollback for the good version. Prefer demoting: the bad image stays addressable for comparison.

## Engine API drift check

`engine-drift.yml` runs daily (06:17 UTC) and on dispatch. It starts the Engine `edge`, compares its `/openapi.json` with `console/engine/openapi.json` (`manage.py engine_schema --check`), and keeps a single **Engine API drift** issue open while the two differ. It comments only when the difference changes, and closes the issue itself once they match again. It never blocks a PR. GitHub pauses scheduled workflows after 60 days without repository activity; re-enable it with `gh workflow enable engine-drift.yml`.
