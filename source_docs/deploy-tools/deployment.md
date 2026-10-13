# Acme Platform Deployment Process Guide

## Overview

The Acme Platform supports multiple deployment strategies for its Kubernetes-based microservices. Each service team selects a strategy based on risk tolerance and traffic characteristics. All deployments are orchestrated through Helm charts with environment-specific value overrides.

## Deployment Strategies

### Blue-Green Deployment

Blue-green deployments maintain two identical environments. Traffic is switched atomically from the current (blue) to the new (green) version after validation.

- Used for stateless services with zero-downtime requirements.
- The platform maintains both environments simultaneously during validation.
- Rollback is instantaneous by switching the service selector back to the blue deployment.

```yaml
# Blue-green service switch via label selector
apiVersion: v1
kind: Service
metadata:
  name: order-service
  namespace: production
spec:
  selector:
    app: order-service
    version: green   # Switch to "blue" for rollback
  ports:
    - protocol: TCP
      port: 80
      targetPort: 8080
```

### Canary Deployment

Canary is the default strategy for most Acme Platform services. A small percentage of traffic is routed to the new version, gradually increasing as confidence grows.

- Initial canary weight: 10% of traffic.
- Promotion steps: 10% -> 25% -> 50% -> 100%.
- Each step requires passing health and latency checks for a minimum of 5 minutes.
- Managed by Flagger with Istio as the service mesh.

```yaml
apiVersion: flagger.app/v1beta1
kind: Canary
metadata:
  name: payment-service
  namespace: production
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: payment-service
  service:
    port: 8080
  analysis:
    interval: 1m
    threshold: 5
    maxWeight: 50
    stepWeight: 10
    metrics:
      - name: request-success-rate
        thresholdRange:
          min: 99
        interval: 1m
      - name: request-duration
        thresholdRange:
          max: 500
        interval: 1m
```

### Rolling Update

Rolling updates replace pods incrementally. This is the simplest strategy and is used for internal tools and non-critical services.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notification-service
spec:
  replicas: 4
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 1
```

## Kubernetes Deployment Manifests

Every service maintains its Kubernetes manifests in a `k8s/` directory within the repository. The standard layout is:

```
k8s/
  base/
    deployment.yaml
    service.yaml
    hpa.yaml
    configmap.yaml
  overlays/
    staging/
      kustomization.yaml
      patches/
    production/
      kustomization.yaml
      patches/
```

Kustomize is used for environment-specific patching on top of the base manifests.

## Helm Chart Usage

The Acme Platform provides a shared Helm chart (`acme-service-chart`) that encapsulates common deployment patterns. Services configure their deployments through `values.yaml` files.

```yaml
# values-production.yaml
replicaCount: 4
image:
  repository: acme-platform.azurecr.io/order-service
  tag: "v2.3.1"
  pullPolicy: IfNotPresent

resources:
  requests:
    cpu: 250m
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 1Gi

autoscaling:
  enabled: true
  minReplicas: 4
  maxReplicas: 12
  targetCPUUtilization: 70

ingress:
  enabled: true
  host: api.acmeplatform.io
  path: /orders
  tls: true

env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: order-service-secrets
        key: database-url
  - name: LOG_LEVEL
    value: "info"
```

### Helm Commands

```bash
# Deploy to staging
helm upgrade --install order-service acme-service-chart \
  --namespace staging \
  --values values-staging.yaml \
  --set image.tag=$(git rev-parse --short HEAD) \
  --wait --timeout 5m

# Deploy to production
helm upgrade --install order-service acme-service-chart \
  --namespace production \
  --values values-production.yaml \
  --set image.tag=v2.3.1 \
  --wait --timeout 10m

# View release history
helm history order-service --namespace production

# Rollback to previous release
helm rollback order-service 0 --namespace production --wait
```

## Pre-Deployment Checks

Before any deployment proceeds, the pipeline runs the following checks:

1. **Image existence** -- Verify the target image tag exists in the container registry.
2. **Config validation** -- Run `helm template` and `kubeval` to validate manifests.
3. **Database migration status** -- Confirm all pending migrations are compatible (no destructive changes without a migration plan).
4. **Dependency health** -- Check that upstream services the deployment depends on are healthy.
5. **Resource quota** -- Verify the target namespace has sufficient resource quota for the deployment.

## Post-Deployment Validation

After deployment, the pipeline executes:

1. **Readiness probe check** -- Wait for all pods to pass readiness probes.
2. **Smoke tests** -- Hit critical API endpoints and validate response codes and schemas.
3. **Metric baseline comparison** -- Compare error rates and latency against the pre-deployment baseline for 10 minutes.
4. **Synthetic transaction** -- Execute a full end-to-end transaction through the service.

```bash
# Post-deploy smoke test example
kubectl exec -n production deploy/order-service -- \
  curl -sf http://localhost:8080/health | jq .status

# Check rollout status
kubectl rollout status deployment/order-service -n production --timeout=300s
```

## Rollback Procedures

### Automated Rollback
Flagger automatically rolls back canary deployments when analysis thresholds are breached. The rollback triggers when:
- Request success rate drops below 99% for 5 consecutive checks.
- P99 latency exceeds 500ms for 5 consecutive checks.

### Manual Rollback

```bash
# Kubernetes native rollback
kubectl rollout undo deployment/order-service -n production

# Helm rollback to specific revision
helm rollback order-service 42 --namespace production

# Emergency: scale down new version, scale up old
kubectl scale deployment/order-service-canary --replicas=0 -n production
```

### Rollback Checklist
- Notify the team in `#deployments` Slack channel.
- Verify the rollback completed and pods are healthy.
- Check database state -- if migrations ran, determine if a backward migration is needed.
- File a post-incident report within 24 hours.

## Database Migration Handling

Database migrations are decoupled from application deployments:

1. **Forward-only migrations** -- All migrations must be backward compatible. No column drops or renames in the same release as the code change.
2. **Migration jobs** -- Migrations run as Kubernetes Jobs before the deployment starts. The job must complete successfully before pods are updated.
3. **Two-phase migrations** -- Breaking schema changes follow a two-release cycle: Release 1 adds the new schema, Release 2 removes the old schema.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: order-service-migration
  namespace: production
spec:
  backoffLimit: 3
  template:
    spec:
      containers:
        - name: migrate
          image: acme-platform.azurecr.io/order-service:v2.3.1
          command: ["python", "-m", "alembic", "upgrade", "head"]
          envFrom:
            - secretRef:
                name: order-service-secrets
      restartPolicy: Never
```
