# Acme Platform Container Orchestration Guide

## Overview

The Acme Platform runs all production workloads on Kubernetes (AKS -- Azure Kubernetes Service). The cluster architecture is designed for multi-tenant microservice deployments with strict resource isolation, automated scaling, and standardized networking.

## Kubernetes Architecture at Acme Platform

The platform operates two AKS clusters:

- **acme-prod-eastus** -- Primary production cluster (East US region)
- **acme-prod-westus** -- Disaster recovery cluster (West US region)

Each cluster is configured with:
- Kubernetes version: 1.29
- Node pools: system, general-purpose, memory-optimized, GPU (for ML workloads)
- CNI: Azure CNI with Cilium for network policy enforcement
- Service mesh: Istio for mTLS, traffic management, and observability

### Node Pool Configuration

| Pool | VM Size | Min Nodes | Max Nodes | Purpose |
|------|---------|-----------|-----------|---------|
| system | Standard_D4s_v5 | 3 | 5 | Kubernetes system components |
| general | Standard_D8s_v5 | 5 | 30 | Standard workloads |
| memory | Standard_E8s_v5 | 2 | 15 | Memory-intensive services (caching, search) |
| gpu | Standard_NC6s_v3 | 0 | 4 | ML inference workloads |

## Pod Specifications

All pods follow the Acme Platform pod security standards. A typical pod spec includes resource management, health probes, and security context.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: order-service
  namespace: production
  labels:
    app: order-service
    version: v2.3.1
    team: commerce
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "8080"
    prometheus.io/path: "/metrics"
spec:
  serviceAccountName: order-service
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    fsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: order-service
      image: acme-platform.azurecr.io/order-service:v2.3.1
      ports:
        - containerPort: 8080
          name: http
          protocol: TCP
        - containerPort: 9090
          name: grpc
          protocol: TCP
      resources:
        requests:
          cpu: 250m
          memory: 512Mi
        limits:
          cpu: 1000m
          memory: 1Gi
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop:
            - ALL
      livenessProbe:
        httpGet:
          path: /health
          port: http
        initialDelaySeconds: 10
        periodSeconds: 15
        failureThreshold: 3
      readinessProbe:
        httpGet:
          path: /ready
          port: http
        initialDelaySeconds: 5
        periodSeconds: 10
        failureThreshold: 2
      volumeMounts:
        - name: config
          mountPath: /etc/config
          readOnly: true
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: config
      configMap:
        name: order-service-config
    - name: tmp
      emptyDir: {}
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: topology.kubernetes.io/zone
      whenUnsatisfiable: DoNotSchedule
      labelSelector:
        matchLabels:
          app: order-service
```

## Service Definitions

Services expose pods to the cluster network and, optionally, to external traffic through ingress.

### ClusterIP Service (Internal)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: order-service
  namespace: production
  labels:
    app: order-service
spec:
  type: ClusterIP
  selector:
    app: order-service
  ports:
    - name: http
      port: 80
      targetPort: 8080
      protocol: TCP
    - name: grpc
      port: 9090
      targetPort: 9090
      protocol: TCP
```

### Headless Service (StatefulSet Discovery)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: cache-service-headless
  namespace: production
spec:
  type: ClusterIP
  clusterIP: None
  selector:
    app: cache-service
  ports:
    - name: redis
      port: 6379
      targetPort: 6379
```

## Deployment Strategies

Deployments use the standard Kubernetes Deployment resource with strategy configuration appropriate to the service type. See `deployment.md` for detailed strategy descriptions.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: production
  labels:
    app: order-service
    version: v2.3.1
spec:
  replicas: 4
  revisionHistoryLimit: 5
  selector:
    matchLabels:
      app: order-service
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 25%
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: order-service
        version: v2.3.1
    spec:
      # Pod spec as defined above
      terminationGracePeriodSeconds: 30
```

The `terminationGracePeriodSeconds` allows in-flight requests to complete before the pod is killed. Services must handle `SIGTERM` by stopping new request acceptance and draining existing connections.

## Horizontal Pod Autoscaler (HPA)

All production services must have an HPA configured. The platform supports scaling on CPU, memory, and custom Prometheus metrics.

```yaml
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
  minReplicas: 4
  maxReplicas: 20
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
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "1000"
```

### Scaling Guidelines

- **minReplicas** should be at least 3 for zone redundancy.
- **maxReplicas** should be set based on the service's capacity plan and budget allocation.
- Scale-up is aggressive (50% increase per minute) to handle traffic spikes.
- Scale-down is conservative (25% decrease every 2 minutes, with 5-minute stabilization) to prevent flapping.

## Resource Requests and Limits

Resource management is critical for cluster stability and cost efficiency.

### Resource Tiers

| Tier | CPU Request | CPU Limit | Memory Request | Memory Limit | Use Case |
|------|-------------|-----------|----------------|--------------|----------|
| Small | 100m | 500m | 128Mi | 256Mi | Internal tools, cron jobs |
| Medium | 250m | 1000m | 512Mi | 1Gi | Standard API services |
| Large | 500m | 2000m | 1Gi | 2Gi | Data processing services |
| XLarge | 1000m | 4000m | 2Gi | 4Gi | Search, ML inference |

### Resource Quotas Per Namespace

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: production-quota
  namespace: production
spec:
  hard:
    requests.cpu: "100"
    requests.memory: 200Gi
    limits.cpu: "200"
    limits.memory: 400Gi
    pods: "500"
    services: "100"
    persistentvolumeclaims: "50"
```

Teams that need to exceed their quota must submit a capacity request through the platform portal.

## Ingress Configuration

External traffic enters the cluster through an NGINX Ingress Controller backed by Azure Load Balancer. TLS termination occurs at the ingress level.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: acme-api-ingress
  namespace: production
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
    nginx.ingress.kubernetes.io/rate-limit-connections: "100"
    nginx.ingress.kubernetes.io/rate-limit-rps: "50"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - api.acmeplatform.io
      secretName: acme-api-tls
  rules:
    - host: api.acmeplatform.io
      http:
        paths:
          - path: /orders
            pathType: Prefix
            backend:
              service:
                name: order-service
                port:
                  number: 80
          - path: /payments
            pathType: Prefix
            backend:
              service:
                name: payment-service
                port:
                  number: 80
          - path: /users
            pathType: Prefix
            backend:
              service:
                name: user-service
                port:
                  number: 80
          - path: /notifications
            pathType: Prefix
            backend:
              service:
                name: notification-service
                port:
                  number: 80
```

## Namespace Organization

The Acme Platform uses namespaces to isolate environments and teams:

| Namespace | Purpose |
|-----------|---------|
| `production` | Live production workloads |
| `staging` | Pre-production environment |
| `dev` | Shared development environment |
| `observability` | Prometheus, Grafana, Jaeger, ELK |
| `istio-system` | Istio control plane |
| `cert-manager` | TLS certificate management |
| `ingress-nginx` | Ingress controller |
| `vault` | HashiCorp Vault |

Each namespace has:
- Resource quotas to prevent runaway resource consumption.
- Network policies restricting inter-namespace communication (deny-all by default, allow-list for known dependencies).
- RBAC roles scoped to the team that owns the namespace.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: production
spec:
  podSelector: {}
  policyTypes:
    - Ingress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-from-ingress
  namespace: production
spec:
  podSelector:
    matchLabels:
      exposed: "true"
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              name: ingress-nginx
      ports:
        - protocol: TCP
          port: 8080
```
