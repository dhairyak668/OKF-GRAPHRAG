---
id: container-orchestration
title: Container Orchestration
type: Pattern
status: stable
tags: [kubernetes, containers, orchestration, infrastructure]
links:
  - target: deployment-process
    relation: used-by
    label: Deployment orchestration
  - target: ci-cd-pipeline
    relation: used-by
    label: Build orchestration
  - target: api-gateway
    relation: hosts
    label: Gateway hosting
sources:
  - id: container-doc
    resource: /source_docs/deploy-tools/containers.md
    title: Container Orchestration Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Container Orchestration

The Acme Platform runs all microservices on Kubernetes. Container orchestration manages the lifecycle of containerized services including scheduling, scaling, networking, and self-healing. It is the runtime foundation for the [Deployment Process](../workflows/deployment-process.md) and the [CI/CD Pipeline](../workflows/ci-cd-pipeline.md).

## Cluster Architecture

The platform operates two Kubernetes clusters:

| Cluster | Purpose | Node count | Instance type |
|---------|---------|-----------|---------------|
| `acme-staging` | Pre-production testing | 6 | m6i.xlarge |
| `acme-production` | Production workloads | 18 | m6i.2xlarge |

Both clusters run Kubernetes 1.29 and are managed through EKS. Node groups are configured with cluster autoscaler for dynamic capacity.

## Pod Specification

Every Acme Platform service runs as a Kubernetes Deployment. A standard pod spec includes resource limits, health probes, and security context.

```yaml
# k8s/base/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: production
  labels:
    app: order-service
    team: commerce
    version: v1.8.3
spec:
  replicas: 3
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
        version: v1.8.3
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
    spec:
      serviceAccountName: order-service
      securityContext:
        runAsNonRoot: true
        fsGroup: 1000
      containers:
        - name: order-service
          image: registry.acme-platform.io/order-service:v1.8.3
          ports:
            - containerPort: 8080
              name: http
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: 1000m
              memory: 512Mi
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            initialDelaySeconds: 15
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: order-service-secrets
                  key: database-url
            - name: KAFKA_BROKERS
              valueFrom:
                configMapKeyRef:
                  name: shared-config
                  key: kafka-brokers
```

## Services and Networking

Kubernetes Services expose pods to the cluster network. The [API Gateway](../services/api-gateway.md) is exposed externally via a LoadBalancer service; internal services use ClusterIP.

```yaml
# k8s/base/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: order-service
  namespace: production
spec:
  type: ClusterIP
  selector:
    app: order-service
  ports:
    - port: 80
      targetPort: http
      protocol: TCP
      name: http
```

### Ingress

External traffic enters through an NGINX Ingress Controller that routes to the API Gateway:

```yaml
# k8s/base/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: acme-platform-ingress
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.acme-platform.io
      secretName: acme-platform-tls
  rules:
    - host: api.acme-platform.io
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api-gateway
                port:
                  number: 80
```

## Horizontal Pod Autoscaler

Services scale automatically based on CPU utilization and custom metrics. The HPA adjusts replica count within configured bounds.

```yaml
# k8s/base/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: order-service-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: order-service
  minReplicas: 3
  maxReplicas: 15
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "100"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 25
          periodSeconds: 120
```

## Resource Management

### Resource Quotas

Each namespace has resource quotas to prevent runaway resource consumption:

```yaml
# k8s/base/resource-quota.yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: team-commerce-quota
  namespace: production
spec:
  hard:
    requests.cpu: "20"
    requests.memory: 40Gi
    limits.cpu: "40"
    limits.memory: 80Gi
    pods: "100"
```

### Limit Ranges

Default resource requests and limits are applied to pods that do not specify them:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: default-limits
  namespace: production
spec:
  limits:
    - default:
        cpu: 500m
        memory: 256Mi
      defaultRequest:
        cpu: 100m
        memory: 128Mi
      type: Container
```

## Namespace Organization

The cluster is organized into namespaces by environment and team:

| Namespace | Purpose |
|-----------|---------|
| `production` | Live production workloads |
| `staging` | Pre-production testing |
| `monitoring` | Prometheus, Grafana, Loki |
| `ingress` | Ingress controllers |
| `cert-manager` | TLS certificate management |

## Configuration Management

Configuration is managed through ConfigMaps and Secrets, applied via Kustomize overlays:

```
k8s/
  base/
    deployment.yaml
    service.yaml
    hpa.yaml
    kustomization.yaml
  overlays/
    staging/
      kustomization.yaml
      replicas-patch.yaml
    production/
      kustomization.yaml
      replicas-patch.yaml
      hpa-patch.yaml
```

```yaml
# k8s/overlays/production/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
patchesStrategicMerge:
  - replicas-patch.yaml
  - hpa-patch.yaml
configMapGenerator:
  - name: shared-config
    literals:
      - kafka-brokers=kafka-0.production.svc:9092,kafka-1.production.svc:9092
```

## Related

- [Deployment Process](../workflows/deployment-process.md) -- uses Kubernetes for rollout strategies
- [CI/CD Pipeline](../workflows/ci-cd-pipeline.md) -- builds container images deployed to the cluster
- [API Gateway](../services/api-gateway.md) -- hosted and scaled by this orchestration layer
