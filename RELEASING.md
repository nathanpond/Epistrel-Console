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
