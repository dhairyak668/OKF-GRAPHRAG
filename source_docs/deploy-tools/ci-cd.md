# Acme Platform CI/CD Pipeline Guide

## Overview

The Acme Platform CI/CD pipeline automates the build, test, and deployment lifecycle for all microservices. Every service repository uses GitHub Actions as the orchestration layer, with standardized workflow templates maintained by the Platform Engineering team. The pipeline enforces quality gates at each stage before promoting artifacts toward production.

All pipelines follow a four-stage model: **Build**, **Test**, **Security Scan**, and **Deploy**. Failures at any stage halt progression and notify the responsible team via Slack and PagerDuty.

## GitHub Actions Workflow Structure

Each microservice repository contains a `.github/workflows/` directory with the following standard workflows:

- `ci.yml` -- triggered on every push and pull request
- `cd-staging.yml` -- triggered on merge to `develop`
- `cd-production.yml` -- triggered on merge to `main` or via manual dispatch
- `rollback.yml` -- manual workflow for emergency rollbacks

### Workflow Template Example

```yaml
name: CI Pipeline

on:
  push:
    branches: [develop, main, 'feature/**']
  pull_request:
    branches: [develop, main]

env:
  REGISTRY: acme-platform.azurecr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      image-tag: ${{ steps.meta.outputs.tags }}
    steps:
      - uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Cache Docker layers
        uses: actions/cache@v4
        with:
          path: /tmp/.buildx-cache
          key: ${{ runner.os }}-buildx-${{ github.sha }}
          restore-keys: |
            ${{ runner.os }}-buildx-
      - name: Log in to ACR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
          cache-from: type=local,src=/tmp/.buildx-cache
          cache-to: type=local,dest=/tmp/.buildx-cache-new
```

## Build Stage

The build stage compiles the application, resolves dependencies, and produces a Docker image. Key details:

- **Base images** are pulled from the internal Acme Platform registry to avoid rate limits on Docker Hub.
- **Dependency caching** uses GitHub Actions cache for pip, npm, and Go modules. Cache keys are derived from lockfile hashes.
- **Multi-stage Dockerfiles** are required. The build stage installs dev dependencies; the runtime stage copies only production artifacts.
- **Image tagging** uses the Git SHA for traceability. Semantic version tags are applied only during release workflows.

## Test Stage

Testing runs in three tiers, each as a separate job with its own container environment:

1. **Unit tests** -- run inside the build container. Must achieve at least 80% line coverage. Coverage reports are uploaded as workflow artifacts.
2. **Integration tests** -- spin up service dependencies (databases, message brokers) using Docker Compose. The `docker-compose.test.yml` file in each repo defines the test topology.
3. **End-to-end tests** -- executed against a dedicated ephemeral environment provisioned by the platform. E2E tests use the Acme Platform test harness (`acme-e2e-runner`) which manages environment lifecycle.

```yaml
  test-unit:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run unit tests
        run: make test-unit
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage/

  test-integration:
    needs: build
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: testdb
          POSTGRES_PASSWORD: testpass
        ports:
          - 5432:5432
      redis:
        image: redis:7
        ports:
          - 6379:6379
    steps:
      - uses: actions/checkout@v4
      - name: Run integration tests
        run: make test-integration
        env:
          DATABASE_URL: postgresql://postgres:testpass@localhost:5432/testdb
          REDIS_URL: redis://localhost:6379
```

## Security Scanning Stage

All images and code undergo security analysis before deployment:

- **SAST (Static Application Security Testing)** -- Semgrep runs against the source code with Acme Platform custom rulesets. Findings of severity HIGH or CRITICAL block the pipeline.
- **Container scanning** -- Trivy scans the built Docker image for OS and library vulnerabilities. The allowlist is maintained in `.trivyignore` and reviewed quarterly.
- **Dependency audit** -- `npm audit`, `pip-audit`, or `govulncheck` depending on the language runtime.
- **Secret scanning** -- TruffleHog scans the commit history to prevent accidental credential leaks.

## Deploy Stage

Deployment is a two-phase process:

### Staging Deployment
- Automatically triggered when code merges to `develop`.
- Deploys to the `staging` Kubernetes namespace.
- Runs smoke tests after deployment to validate basic functionality.
- Staging must pass all smoke tests before production deployment is unblocked.

### Production Deployment
- Triggered by merging `develop` into `main`, or via manual workflow dispatch.
- Uses canary deployment strategy by default (see `deployment.md` for details).
- Requires at least one approval from a team lead.
- Deployment metadata is recorded in the Acme Platform deployment ledger for audit.

## Branch Strategies

The Acme Platform uses a modified GitFlow strategy:

| Branch | Purpose | Deploys To |
|--------|---------|------------|
| `main` | Production-ready code | Production |
| `develop` | Integration branch | Staging |
| `feature/*` | Feature development | Ephemeral env (PR) |
| `hotfix/*` | Emergency fixes | Fast-track to production |
| `release/*` | Release candidates | Pre-production |

Feature branches must be rebased on `develop` before merging. Squash merges are enforced for feature branches to keep the history clean.

## Rollback Procedures

Rollbacks can be initiated in two ways:

1. **Automated rollback** -- If post-deployment health checks fail for more than 3 consecutive minutes, the pipeline automatically rolls back to the previous stable image tag.
2. **Manual rollback** -- Trigger the `rollback.yml` workflow with the target image tag. This workflow skips build and test stages but still runs security validation.

```bash
# Manual rollback via GitHub CLI
gh workflow run rollback.yml \
  -f target_tag=abc123def \
  -f environment=production \
  -f reason="Elevated error rate after deploy"
```

Rollback events are automatically posted to the `#platform-incidents` Slack channel and create an entry in the incident log.
