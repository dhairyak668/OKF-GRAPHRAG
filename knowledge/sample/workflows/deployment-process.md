---
id: deployment-process
title: Deployment Process
type: Process
status: stable
tags: [deployment, kubernetes, rollout]
links:
  - target: ci-cd-pipeline
    relation: triggered-by
    label: Pipeline trigger
  - target: container-orchestration
    relation: uses
    label: K8s deployment
  - target: api-gateway
    relation: deploys
    label: Gateway rollout
  - target: monitoring-setup
    relation: monitored-by
    label: Deploy monitoring
sources:
  - id: deploy-doc
    resource: /source_docs/deploy-tools/deployment.md
    title: Deployment Process Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Deployment Process

The Acme Platform deployment process manages the rollout of microservices to Kubernetes clusters. All deployments are triggered by the [CI/CD Pipeline](../workflows/ci-cd-pipeline.md) and orchestrated through [Container Orchestration](../concepts/container-orchestration.md).

## Deployment Strategies

The platform supports three deployment strategies, selected per service based on criticality and traffic volume.

### Blue-Green Deployment

Blue-green deployments maintain two identical environments. Traffic is switched atomically from the active (blue) environment to the new (green) environment after validation.

```yaml
# k8s/blue-green-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway-green
  namespace: production
  labels:
    app: api-gateway
    slot: green
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api-gateway
      slot: green
  template:
    metadata:
      labels:
        app: api-gateway
        slot: green
    spec:
      containers:
        - name: api-gateway
          image: registry.acme-platform.io/api-gateway:v2.4.1
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
```

Once the green deployment is healthy, the service is switched by updating the Kubernetes Service selector:

```bash
kubectl patch service api-gateway \
  -p '{"spec":{"selector":{"slot":"green"}}}' \
  --namespace=production
```

### Canary Releases

Canary releases gradually shift traffic from the old version to the new version. The [API Gateway](../services/api-gateway.md) manages traffic splitting via weighted routing rules.

```yaml
# k8s/canary-release.yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: user-service-canary
spec:
  hosts:
    - user-service
  http:
    - route:
        - destination:
            host: user-service
            subset: stable
          weight: 90
        - destination:
            host: user-service
            subset: canary
          weight: 10
```

The canary weight increases in steps (10% -> 25% -> 50% -> 100%) with automated rollback if error rates exceed thresholds.

### Rolling Updates

Rolling updates are the default strategy for non-critical services. Kubernetes replaces pods incrementally.

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

## Health Checks

Every service must expose two health endpoints for Kubernetes probes:

| Endpoint | Probe Type | Purpose |
|----------|-----------|---------|
| `/healthz` | Liveness | Is the process alive? Restart if not. |
| `/readyz` | Readiness | Can it serve traffic? Remove from LB if not. |

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /readyz
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 2
```

## Rollback Procedures

If a deployment fails health checks or triggers alerts in the [Monitoring Setup](../workflows/monitoring-setup.md), an automatic rollback is initiated.

### Automatic Rollback

The deployment controller watches for failed rollouts and reverts automatically:

```bash
# Check rollout status
kubectl rollout status deployment/user-service --namespace=production --timeout=300s

# If failed, rollback to previous revision
kubectl rollout undo deployment/user-service --namespace=production
```

### Manual Rollback

For cases where automated rollback does not trigger, operators can roll back manually:

```bash
# View rollout history
kubectl rollout history deployment/user-service --namespace=production

# Roll back to a specific revision
kubectl rollout undo deployment/user-service --to-revision=3 --namespace=production
```

## Pre-Deployment Checklist

Before any production deployment, the pipeline verifies:

1. All tests pass with coverage above threshold
2. Security scans report no critical vulnerabilities
3. Database migrations (if any) have been applied to staging
4. Staging environment smoke tests pass
5. Rollback plan is documented for the release

## Post-Deployment Verification

After deployment, the pipeline runs a verification suite:

```bash
#!/bin/bash
# post-deploy-verify.sh
SERVICE_URL="https://api.acme-platform.io"

# Check health endpoint
curl -sf "$SERVICE_URL/healthz" || exit 1

# Verify version
DEPLOYED_VERSION=$(curl -s "$SERVICE_URL/version" | jq -r '.version')
echo "Deployed version: $DEPLOYED_VERSION"

# Run smoke tests
npm run test:smoke -- --base-url="$SERVICE_URL"
```

## Related

- [CI/CD Pipeline](../workflows/ci-cd-pipeline.md) -- triggers this deployment process
- [Container Orchestration](../concepts/container-orchestration.md) -- underlying Kubernetes management
- [API Gateway](../services/api-gateway.md) -- primary service deployed via this process
- [Monitoring Setup](../workflows/monitoring-setup.md) -- observes deployment health and triggers rollbacks
