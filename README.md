# es6kr Reusable Workflows & Org Templates

Centralized repository for organization-wide GitHub Actions Reusable Workflows and default templates for the `es6kr` organization.

## Available Workflows

### 1. `reusable-ci.yml` (`@v1`)

Standardized CI workflow providing automated testing, security pre-checkout checks for self-hosted runners, and BuildKit container packaging to GitHub Container Registry (GHCR).

The compatibility example below retains the existing `@v1` contract. To use the
optional platform and result interfaces added here, pin an immutable reviewed
commit that contains them. This change does not move the `v1` tag.

#### Usage in Consumer Repositories

```yaml
name: build-and-push

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, labeled, unlabeled]

jobs:
  ci:
    uses: es6kr/.github/.github/workflows/reusable-ci.yml@v1
    with:
      test-command: bun test
      image-name: ghcr.io/es6kr/clawo-webhook
      docker-context: .
    secrets: inherit
```

#### Inputs

| Input | Type | Required | Default | Description |
|---|---|---|---|---|
| `test-command` | string | false | `""` | Command to execute for unit testing |
| `test-runtime` | string | false | `"bun"` | Runtime environment (`bun`, `node`, `none`) |
| `image-name` | string | **true** | - | Target GHCR container image name |
| `docker-context` | string | false | `"."` | Docker build context directory |
| `dockerfile` | string | false | `"Dockerfile"` | Path to Dockerfile relative to context |
| `platforms` | string | false | `""` | Optional Buildx target platforms, e.g. `linux/arm64`; empty preserves native selection |
| `oci-artifact-name` | string | false | `""` | Opt-in PR-only OCI archive prefix; empty preserves existing exports |
| `push-image` | boolean | false | `false` | Force push image (defaults to push on main/tags) |
| `runner-label` | string | false | `"es6kr-local-linux"` | Target self-hosted runner label |
| `timeout-minutes` | number | false | `15` | Timeout in minutes |

#### Build identifiers

`image-digest` forwards the builder's digest output; `image-id` forwards its local
image identifier. They are distinct values. Neither independently proves a
registry subject's media type, publication, signature or runtime acceptance.
Successful explicit-platform builds also emit `CI_BUILD_RECEIPT` with the actual
checkout commit, run ID/attempt and both identifiers. OCI subject kind remains
`unverified` and `registry_verified` remains false until independent read-back.

#### Optional nonpublishing OCI evidence

Callers pinned to a reviewed commit supporting `oci-artifact-name` may request
an OCI archive on pull requests with `push-image: false`. Leave the input empty
on push events and for all legacy callers. For example, in the caller's `with`
mapping use `oci-artifact-name: ${{ github.event_name == 'pull_request' && 'clawo-oci' || '' }}`.
Do not add this input to the unchanged `@v1` compatibility example above.

The prefix must contain only letters, digits, underscores, dots and hyphens,
start with a letter or digit, and be at most 80 characters. Export goes to a
new runner-owned temporary directory. The no-overwrite artifact contains only
`image.oci.tar` and `build-receipt.json`, is retained for three days and uses no
artifact compression. The archive is limited to 2,000,000,000 bytes, leaving
room within the approved 2 GiB download envelope for its wrapper and receipt.

The receipt binds the actual checkout commit, run ID/attempt, builder outputs,
archive SHA256 and byte size. An independent consumer must verify the exact
run/head and archive hash before inspecting the OCI index, platform manifest
and config digests. An uploaded archive is not registry publication, signature
verification, deployment or native skill-load acceptance. This option changes
neither the existing push expression nor any runner route.

#### Runner Routing & Security Policy

- **Push events on `main` / tags (`v*`)**: Automatically dispatched to `es6kr-local-linux` runner to prevent hosted-runner spending limit admission failures.
- **Pull Request events**: Runs on `es6kr-local-linux` if labeled `ci:local-linux` on trusted same-repository branches; otherwise uses default hosted runner.
- **Fork PR Defense**: Rejects attempts from outside forks requesting self-hosted runners before checkout.
