---
id: sdk-client
title: Platform SDK Client
type: Tool
status: stable
tags: [sdk, client, python, api]
links:
  - target: api-gateway
    relation: communicates-with
    label: API communication
  - target: auth-service
    relation: authenticates-via
    label: Authentication
  - target: notification-engine
    relation: integrates-with
    label: Notification access
sources:
  - id: sdk-readme
    resource: /source_docs/sdk-client/README.md
    title: SDK Client README
generated:
  by: "human:author"
  at: "2026-10-01T00:00:00Z"
---

# Platform SDK Client

The Acme Platform SDK is the official Python client for interacting with all Acme Platform services. It provides a unified interface for authentication, resource management, event publishing, and notification handling across the entire platform.

## Installation

Install the SDK from the internal PyPI registry:

```bash
pip install acme-platform-sdk
```

For development with optional dependencies:

```bash
pip install acme-platform-sdk[dev,test]
```

## Quickstart

```python
from acme_sdk import AcmeClient

client = AcmeClient(
    base_url="https://api.acmeplatform.io",
    client_id="your-client-id",
    client_secret="your-client-secret",
)

# List available services
services = client.services.list()
for svc in services:
    print(f"{svc.name} — {svc.status}")
```

## Client Initialization

The `AcmeClient` accepts several configuration options that control its behavior:

```python
from acme_sdk import AcmeClient, RetryPolicy

client = AcmeClient(
    base_url="https://api.acmeplatform.io",
    client_id="your-client-id",
    client_secret="your-client-secret",
    timeout=30,
    retry_policy=RetryPolicy(max_retries=3, backoff_factor=0.5),
    enable_telemetry=True,
)
```

| Parameter          | Type          | Default | Description                          |
|--------------------|---------------|---------|--------------------------------------|
| `base_url`         | `str`         | —       | Base URL of the API Gateway          |
| `client_id`        | `str`         | —       | OAuth2 client identifier             |
| `client_secret`    | `str`         | —       | OAuth2 client secret                 |
| `timeout`          | `int`         | `30`    | Request timeout in seconds           |
| `retry_policy`     | `RetryPolicy` | `None`  | Custom retry configuration           |
| `enable_telemetry` | `bool`        | `False` | Send usage metrics to platform       |

The client authenticates through the [Auth Service](../services/auth-service.md) and routes all requests through the [API Gateway](../services/api-gateway.md).

## Making API Calls

### Resource CRUD Operations

```python
# Create a resource
project = client.projects.create(name="my-project", region="us-east-1")

# Retrieve a resource
project = client.projects.get(project_id="proj-abc123")

# Update a resource
project.update(name="renamed-project")

# Delete a resource
client.projects.delete(project_id="proj-abc123")
```

### Pagination

All list endpoints return paginated results. Use the built-in iterator for automatic pagination:

```python
# Automatic pagination
for deployment in client.deployments.list(project_id="proj-abc123"):
    print(deployment.id, deployment.status)

# Manual pagination
page = client.deployments.list(project_id="proj-abc123", page_size=50)
while page:
    for item in page.items:
        process(item)
    page = page.next_page()
```

## Error Handling

The SDK raises typed exceptions for different failure modes:

```python
from acme_sdk.exceptions import (
    AuthenticationError,
    RateLimitError,
    ResourceNotFoundError,
    ServerError,
)

try:
    resource = client.projects.get(project_id="proj-missing")
except ResourceNotFoundError as e:
    print(f"Project not found: {e.resource_id}")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after} seconds.")
except AuthenticationError:
    print("Invalid or expired credentials.")
except ServerError as e:
    print(f"Platform error [{e.status_code}]: {e.message}")
```

## Notification Integration

The SDK provides direct access to the [Notification Engine](../services/notification-engine.md) for sending alerts and messages:

```python
from acme_sdk.notifications import Channel

client.notifications.send(
    channel=Channel.EMAIL,
    recipients=["ops-team@acme.io"],
    subject="Deployment Complete",
    body="Service user-api deployed to production.",
)
```

## Related Documentation

- [API Gateway](../services/api-gateway.md) — request routing and rate limiting
- [Auth Service](../services/auth-service.md) — identity and access management
- [Notification Engine](../services/notification-engine.md) — multi-channel notifications
