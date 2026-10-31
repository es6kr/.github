# es6kr Reusable Workflows & Org Templates

Centralized repository for organization-wide GitHub Actions Reusable Workflows and default templates for the `es6kr` organization.

## Available Workflows

### 1. `reusable-ci.yml` (`@v1`)

Standardized CI workflow providing automated testing, security pre-checkout checks for self-hosted runners, and BuildKit container packaging to GitHub Container Registry (GHCR).

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
| `push-image` | boolean | false | `false` | Force push image (defaults to push on main/tags) |
| `runner-label` | string | false | `"es6kr-local-linux"` | Target self-hosted runner label |
| `timeout-minutes` | number | false | `15` | Timeout in minutes |

#### Runner Routing & Security Policy

- **Push events on `main` / tags (`v*`)**: Automatically dispatched to `es6kr-local-linux` runner to prevent hosted-runner spending limit admission failures.
- **Pull Request events**: Runs on `es6kr-local-linux` if labeled `ci:local-linux` on trusted same-repository branches; otherwise uses default hosted runner.
- **Fork PR Defense**: Rejects attempts from outside forks requesting self-hosted runners before checkout.
