# Acme Platform Configuration Management Guide

## Overview

Configuration management on the Acme Platform follows a layered approach that separates application defaults, environment-specific settings, and runtime overrides. All secrets are managed through HashiCorp Vault, while non-sensitive configuration is stored in Kubernetes ConfigMaps and environment variables.

## Environment Variable Management

Services consume configuration through environment variables, following the twelve-factor app methodology. Variables are injected at runtime via Kubernetes manifests and never baked into container images.

### Naming Convention

All environment variables follow the pattern `ACME_{SERVICE}_{SETTING}`:

```
ACME_ORDER_DATABASE_URL=postgresql://order-db:5432/orders
ACME_ORDER_CACHE_TTL=300
ACME_ORDER_LOG_LEVEL=info
ACME_ORDER_MAX_RETRIES=3
ACME_ORDER_FEATURE_ASYNC_PROCESSING=true
```

### Kubernetes ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: order-service-config
  namespace: production
data:
  ACME_ORDER_LOG_LEVEL: "info"
  ACME_ORDER_CACHE_TTL: "300"
  ACME_ORDER_MAX_RETRIES: "3"
  ACME_ORDER_RATE_LIMIT_RPS: "1000"
  ACME_ORDER_PAGINATION_MAX_SIZE: "100"
  ACME_ORDER_GRPC_TIMEOUT_MS: "5000"
```

### Injecting into Pods

```yaml
spec:
  containers:
    - name: order-service
      envFrom:
        - configMapRef:
            name: order-service-config
        - secretRef:
            name: order-service-secrets
      env:
        - name: ACME_ORDER_POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: ACME_ORDER_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
```

## Secrets Management with HashiCorp Vault

All sensitive configuration (database credentials, API keys, certificates) is stored in HashiCorp Vault. The Acme Platform uses the Vault Agent Injector to deliver secrets to pods.

### Vault Path Structure

```
secret/acme-platform/
  production/
    order-service/
      database        # DATABASE_URL, DATABASE_PASSWORD
      redis           # REDIS_URL, REDIS_PASSWORD
      api-keys        # PAYMENT_GATEWAY_KEY, NOTIFICATION_API_KEY
    payment-service/
      ...
  staging/
    order-service/
      ...
```

### Vault Agent Injector Annotations

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
spec:
  template:
    metadata:
      annotations:
        vault.hashicorp.com/agent-inject: "true"
        vault.hashicorp.com/role: "order-service"
        vault.hashicorp.com/agent-inject-secret-db: "secret/acme-platform/production/order-service/database"
        vault.hashicorp.com/agent-inject-template-db: |
          {{- with secret "secret/acme-platform/production/order-service/database" -}}
          export DATABASE_URL="{{ .Data.data.url }}"
          export DATABASE_PASSWORD="{{ .Data.data.password }}"
          {{- end -}}
```

### Vault Policy Example

```hcl
# order-service-policy.hcl
path "secret/data/acme-platform/production/order-service/*" {
  capabilities = ["read"]
}

path "secret/data/acme-platform/staging/order-service/*" {
  capabilities = ["read"]
}

# Deny access to other services' secrets
path "secret/data/acme-platform/*/payment-service/*" {
  capabilities = ["deny"]
}
```

## Configuration Hierarchy

Configuration values are resolved in the following order (later entries override earlier ones):

1. **Application defaults** -- Hardcoded in the application source. These provide sensible defaults for local development.
2. **Configuration file** -- A `config.yaml` baked into the Docker image with environment-agnostic defaults.
3. **ConfigMap values** -- Kubernetes ConfigMap injected as environment variables.
4. **Vault secrets** -- Secrets injected by the Vault Agent.
5. **Runtime overrides** -- Values set through the Acme Platform admin API or feature flag system.

### Python Configuration Loader

```python
import os
import yaml
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DatabaseConfig:
    url: str = "postgresql://localhost:5432/dev"
    pool_size: int = 10
    pool_timeout: int = 30
    echo: bool = False


@dataclass
class CacheConfig:
    url: str = "redis://localhost:6379"
    ttl: int = 300
    prefix: str = "acme"


@dataclass
class ServiceConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    log_level: str = "info"
    max_retries: int = 3
    service_name: str = "unknown"


def load_config(config_path: str = "config.yaml") -> ServiceConfig:
    """Load configuration with hierarchy: defaults < file < env vars."""
    config = ServiceConfig()

    # Layer 2: config file
    if os.path.exists(config_path):
        with open(config_path) as f:
            file_config = yaml.safe_load(f)
            if file_config:
                _apply_dict(config, file_config)

    # Layer 3+4: environment variables (includes ConfigMap and Vault)
    prefix = f"ACME_{config.service_name.upper()}_"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            setting = key[len(prefix):].lower()
            _apply_env(config, setting, value)

    return config


def _apply_dict(config: ServiceConfig, data: dict) -> None:
    for key, value in data.items():
        if hasattr(config, key):
            current = getattr(config, key)
            if isinstance(current, (DatabaseConfig, CacheConfig)) and isinstance(value, dict):
                for k, v in value.items():
                    if hasattr(current, k):
                        setattr(current, k, type(getattr(current, k))(v))
            else:
                setattr(config, key, value)


def _apply_env(config: ServiceConfig, setting: str, value: str) -> None:
    if setting.startswith("database_"):
        field_name = setting[len("database_"):]
        if hasattr(config.database, field_name):
            target_type = type(getattr(config.database, field_name))
            setattr(config.database, field_name, target_type(value))
    elif setting.startswith("cache_"):
        field_name = setting[len("cache_"):]
        if hasattr(config.cache, field_name):
            target_type = type(getattr(config.cache, field_name))
            setattr(config.cache, field_name, target_type(value))
    elif hasattr(config, setting):
        target_type = type(getattr(config, setting))
        setattr(config, setting, target_type(value))
```

## Hot Reload Support

The Acme Platform supports hot reloading of configuration without restarting pods. This is implemented through a file-watching mechanism on mounted ConfigMap volumes.

```python
import threading
import hashlib
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class ConfigReloader(FileSystemEventHandler):
    def __init__(self, config_path: str, reload_callback):
        self.config_path = config_path
        self.reload_callback = reload_callback
        self._last_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        content = Path(self.config_path).read_bytes()
        return hashlib.sha256(content).hexdigest()

    def on_modified(self, event):
        if event.src_path == self.config_path:
            new_hash = self._compute_hash()
            if new_hash != self._last_hash:
                self._last_hash = new_hash
                self.reload_callback()


def start_config_watcher(config_path: str, reload_callback):
    handler = ConfigReloader(config_path, reload_callback)
    observer = Observer()
    observer.schedule(handler, path=str(Path(config_path).parent), recursive=False)
    observer.start()
    return observer
```

ConfigMap changes are propagated by Kubernetes within the kubelet sync period (default 60 seconds). The watcher detects the file change and triggers the reload callback.

## Feature Flags

Feature flags on the Acme Platform are managed through the configuration system and can be toggled without redeployment.

### Flag Definition

```yaml
# feature-flags.yaml
flags:
  async_processing:
    description: "Enable asynchronous order processing pipeline"
    default: false
    environments:
      staging: true
      production: false

  new_payment_gateway:
    description: "Route payments through the new gateway"
    default: false
    rollout:
      type: percentage
      value: 25

  enhanced_search:
    description: "Use the new search indexing engine"
    default: false
    environments:
      staging: true
      production: true
```

### Flag Evaluation in Code

```python
from acme_platform.flags import FeatureFlagClient

flags = FeatureFlagClient(service="order-service")

if flags.is_enabled("async_processing"):
    await process_order_async(order)
else:
    process_order_sync(order)

# Percentage-based rollout
if flags.is_enabled("new_payment_gateway", context={"user_id": user_id}):
    gateway = NewPaymentGateway()
else:
    gateway = LegacyPaymentGateway()
```

## Config Validation

All configuration is validated at startup. Services fail fast if required configuration is missing or invalid.

```python
from pydantic import BaseModel, validator, AnyUrl


class DatabaseSettings(BaseModel):
    url: AnyUrl
    pool_size: int = 10
    pool_timeout: int = 30

    @validator("pool_size")
    def validate_pool_size(cls, v):
        if v < 1 or v > 100:
            raise ValueError("pool_size must be between 1 and 100")
        return v


class ServiceSettings(BaseModel):
    database: DatabaseSettings
    log_level: str = "info"
    max_retries: int = 3

    @validator("log_level")
    def validate_log_level(cls, v):
        allowed = {"debug", "info", "warning", "error", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.lower()
```

Validation errors are logged with full context and the pod enters a `CrashLoopBackOff` state, which triggers the `PodCrashLooping` alert in the monitoring stack.
