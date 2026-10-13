---
id: config-manager
title: Configuration Management Library
type: Tool
status: stable
tags: [config, secrets, environment]
links:
  - target: api-gateway-config
    relation: supports
    label: Gateway config management
  - target: deployment-process
    relation: used-in
    label: Deployment config
sources:
  - id: config-doc
    resource: /source_docs/deploy-tools/config-management.md
    title: Config Management Guide
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Configuration Management Library

The Acme Platform configuration management library provides a unified interface for loading, merging, and validating configuration from multiple sources. It supports environment variables, YAML files, secrets vaults, and remote configuration stores.

## Installation

```bash
pip install acme-config
```

## Basic Usage

```python
from acme_config import ConfigManager

config = ConfigManager()
config.load_env()                          # environment variables
config.load_file("/etc/acme/service.yaml") # YAML config file

db_host = config.get("database.host", default="localhost")
db_port = config.get_int("database.port", default=5432)
```

## Configuration Hierarchy

The library merges values from multiple sources in a well-defined order. Later sources override earlier ones:

1. **Built-in defaults** -- hardcoded fallback values
2. **YAML config files** -- `/etc/acme/<service>.yaml`
3. **Environment variables** -- prefixed with `ACME_`
4. **Secrets vault** -- values fetched from HashiCorp Vault or AWS Secrets Manager
5. **Remote config store** -- runtime overrides from the platform config service

```python
config = ConfigManager(prefix="ACME")
config.load_defaults({"database.host": "localhost", "database.port": 5432})
config.load_file("/etc/acme/service.yaml")
config.load_env()
config.load_vault("secret/data/my-service")
```

## Secrets Vault Integration

Retrieve secrets securely without exposing them in environment variables or config files:

```python
from acme_config.vault import VaultProvider

vault = VaultProvider(
    address="https://vault.acmeplatform.io",
    auth_method="kubernetes",
    role="my-service",
)

config.add_provider(vault, path="secret/data/my-service")

db_password = config.get("database.password")  # fetched from Vault
```

The vault provider caches secrets in memory and supports automatic rotation when a secret's lease expires.

## Hot Reload

Enable live configuration updates without restarting the service:

```python
from acme_config import ConfigManager, FileWatcher

config = ConfigManager()
config.load_file("/etc/acme/service.yaml")

watcher = FileWatcher(config, interval_seconds=30)
watcher.start()

# Register a callback for specific keys
@config.on_change("feature_flags.*")
def handle_flag_change(key, old_value, new_value):
    print(f"Flag changed: {key} = {new_value}")
```

Hot reload is used during the [Deployment Process](../workflows/deployment-process.md) to update feature flags and tuning parameters without service restarts.

## Validation

Define a schema to validate configuration at startup:

```python
from acme_config.schema import ConfigSchema, Required, IntRange

schema = ConfigSchema({
    "database.host": Required(str),
    "database.port": IntRange(1024, 65535),
    "database.pool_size": IntRange(1, 100),
})

errors = schema.validate(config)
if errors:
    for e in errors:
        print(f"Config error: {e}")
    sys.exit(1)
```

## Gateway Configuration

The library powers the [API Gateway Configuration](../services/api-gateway-config.md) layer, enabling route rules, rate limits, and upstream definitions to be managed declaratively.

## Related Documentation

- [API Gateway Configuration](../services/api-gateway-config.md) -- declarative gateway config
- [Deployment Process](../workflows/deployment-process.md) -- end-to-end deployment workflow
