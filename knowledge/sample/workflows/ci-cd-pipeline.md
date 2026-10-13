---
id: ci-cd-pipeline
title: CI/CD Pipeline
type: Process
status: stable
tags: [ci, cd, deployment, automation]
links:
  - target: deployment-process
    relation: triggers
    label: Deployment trigger
  - target: api-gateway
    relation: deploys
    label: Gateway deployment
  - target: container-orchestration
    relation: uses
    label: Container builds
sources:
  - id: cicd-doc
    resource: /source_docs/deploy-tools/ci-cd.md
    title: CI/CD Pipeline Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# CI/CD Pipeline

The Acme Platform CI/CD pipeline automates the build, test, and deployment lifecycle for all microservices. It is built on GitHub Actions and follows a trunk-based development model with short-lived feature branches.

## Pipeline Overview

Every push to a feature branch triggers the full pipeline. Merges to `main` additionally trigger the deployment stages. The pipeline enforces quality gates at each stage before advancing.

```
feature branch push --> Build --> Unit Test --> Lint/Scan --> Integration Test
                                                                    |
main merge ----------> Build --> Test --> Scan --> Stage Deploy --> Prod Deploy
```

## Build Stage

The build stage compiles source code, resolves dependencies, and produces container images. Each service has a `Dockerfile` at its repository root.

```yaml
# .github/workflows/ci.yml
name: CI Pipeline
on:
  push:
    branches: [main, 'feature/**']
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Build container image
        run: |
          docker buildx build \
            --tag acme-platform/${{ github.event.repository.name }}:${{ github.sha }} \
            --cache-from type=gha \
            --cache-to type=gha,mode=max \
            --push .
```

Images are tagged with the Git SHA and pushed to the internal container registry. [Container Orchestration](../concepts/container-orchestration.md) pulls these images during deployment.

## Test Stage

Unit tests run in parallel across service modules. The pipeline fails if coverage drops below the configured threshold (currently 80%).

```yaml
  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run unit tests
        run: make test-unit
      - name: Run integration tests
        run: make test-integration
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          fail_ci_if_error: true
          threshold: 80
```

Integration tests spin up dependent services using Docker Compose and validate cross-service contracts.

## Security Scan Stage

Static analysis and vulnerability scanning run after tests pass. The pipeline uses Trivy for container image scanning and Semgrep for code analysis.

```yaml
  scan:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Trivy image scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: acme-platform/${{ github.event.repository.name }}:${{ github.sha }}
          severity: CRITICAL,HIGH
          exit-code: 1
      - name: Semgrep static analysis
        uses: returntocorp/semgrep-action@v1
        with:
          config: p/owasp-top-ten
```

## Deployment Stage

On merge to `main`, the pipeline triggers the [Deployment Process](../workflows/deployment-process.md). The deployment is a two-phase rollout: staging first, then production after manual approval.

```yaml
  deploy-staging:
    needs: scan
    if: github.ref == 'refs/heads/main'
    environment: staging
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to staging
        run: |
          kubectl set image deployment/$SERVICE_NAME \
            $SERVICE_NAME=acme-platform/$SERVICE_NAME:${{ github.sha }} \
            --namespace=staging

  deploy-production:
    needs: deploy-staging
    environment:
      name: production
      url: https://api.acme-platform.io
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to production
        run: |
          kubectl set image deployment/$SERVICE_NAME \
            $SERVICE_NAME=acme-platform/$SERVICE_NAME:${{ github.sha }} \
            --namespace=production
```

The [API Gateway](../services/api-gateway.md) is deployed through the same pipeline but with an additional configuration validation step that checks route definitions before rollout.

## Pipeline Configuration

Each service repository contains a `.github/workflows/ci.yml` file. Shared workflow templates are stored in the `acme-platform/.github` repository and referenced via `uses: acme-platform/.github/.github/workflows/shared-ci.yml@main`.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REGISTRY_URL` | Container registry endpoint | `registry.acme-platform.io` |
| `COVERAGE_THRESHOLD` | Minimum test coverage | `80` |
| `SCAN_SEVERITY` | Trivy scan severity filter | `CRITICAL,HIGH` |
| `DEPLOY_TIMEOUT` | Rollout timeout in seconds | `300` |

## Troubleshooting

- **Build cache miss**: Check that the `cache-from` and `cache-to` parameters reference the correct GHA cache scope.
- **Flaky integration tests**: Ensure dependent services in Docker Compose have health checks and that the test runner waits for readiness.
- **Scan false positives**: Add exceptions to `.trivyignore` or `.semgrepignore` with a justification comment.

## Related

- [Deployment Process](../workflows/deployment-process.md) -- triggered after pipeline completes on main
- [API Gateway](../services/api-gateway.md) -- deployed via this pipeline
- [Container Orchestration](../concepts/container-orchestration.md) -- manages container builds and runtime
