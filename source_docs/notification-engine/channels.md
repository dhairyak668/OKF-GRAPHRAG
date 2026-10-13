# Notification Channels

## Overview

The Notification Engine supports four delivery channels: email, SMS, push notifications, and webhooks. Each channel has its own worker process, provider integration, and configuration. This document covers setup, configuration, and best practices for each channel.

---

## Channel Summary

| Channel   | Primary Provider | Fallback            | Typical Use Cases                              |
|-----------|------------------|---------------------|------------------------------------------------|
| Email     | SendGrid         | SMTP relay          | Transactional emails, reports, account alerts  |
| SMS       | Twilio           | --                  | MFA codes, critical alerts, time-sensitive     |
| Push      | FCM              | --                  | In-app alerts, real-time updates               |
| Webhook   | Custom HTTPS     | --                  | Third-party integrations, automation triggers  |

---

## Email -- SendGrid

SendGrid is the primary email provider for all environments. An SMTP relay is available as a fallback for development or when SendGrid is unreachable.

### SendGrid Configuration

```yaml
email:
  provider: sendgrid
  sendgrid:
    api_key: "${SENDGRID_API_KEY}"
    from_address: "noreply@acme-platform.io"
    from_name: "Acme Platform"
    reply_to: "support@acme-platform.io"
    ip_pool: "transactional"
    sandbox_mode: false             # Set true for testing without actual delivery
    tracking:
      click_tracking: true
      open_tracking: true
      subscription_tracking: false  # Managed by our own preference system
    rate_limit: 100/s               # SendGrid plan-dependent
```

### SendGrid Event Webhook

Configure SendGrid to post delivery events back to the Notification Engine for real-time status tracking:

```
Event Webhook URL: https://notifications.acme-platform.io/webhooks/sendgrid/events
Events to track: delivered, bounce, dropped, deferred, open, click, spam_report, unsubscribe
```

### Bounce and Complaint Handling

The engine automatically processes SendGrid event webhooks to:
- Mark hard-bounced addresses as invalid (suppressed from future sends)
- Track soft bounces and retry delivery
- Record spam complaints and honor unsubscribe requests
- Alert the ops team if the bounce rate exceeds 3% over a rolling 24-hour window

### SMTP Fallback (development or self-hosted)

```yaml
email:
  provider: smtp
  smtp:
    host: smtp.mailserver.internal
    port: 587
    username: "${SMTP_USERNAME}"
    password: "${SMTP_PASSWORD}"
    encryption: starttls    # none, starttls, or ssl
    from_address: "noreply@acme-platform.io"
    from_name: "Acme Platform"
    pool_size: 10
    timeout_ms: 5000
```

### Email Best Practices
- Configure SPF, DKIM, and DMARC DNS records for the sending domain
- Use a dedicated subdomain for transactional email (e.g., `mail.acme-platform.io`)
- Monitor bounce rates via the SendGrid dashboard and the engine's `/metrics` endpoint
- Include an unsubscribe link in all marketing and non-critical emails
- Keep HTML email size under 100KB for reliable rendering across clients

---

## SMS -- Twilio

SMS delivery uses Twilio's Programmable Messaging API. All messages are sent through a Messaging Service for automatic number selection and compliance.

### Twilio Configuration

```yaml
sms:
  provider: twilio
  twilio:
    account_sid: "${TWILIO_ACCOUNT_SID}"
    auth_token: "${TWILIO_AUTH_TOKEN}"
    messaging_service_sid: "MG9a1b2c3d4e5f6a7b8c9d0e1f2a3b4c"
    status_callback_url: "https://notifications.acme-platform.io/webhooks/twilio/status"
    max_price: "0.05"               # USD, reject messages above this per-segment cost
    validity_period: 3600           # Seconds, how long Twilio attempts delivery
```

### Status Callback Events

Twilio posts delivery status updates to the configured callback URL. The engine maps these to internal statuses:

| Twilio Status | Internal Status | Description                  |
|---------------|-----------------|------------------------------|
| `queued`      | `processing`    | Message accepted by Twilio   |
| `sent`        | `sent`          | Handed off to carrier        |
| `delivered`   | `delivered`     | Carrier confirmed delivery   |
| `undelivered` | `failed`        | Carrier could not deliver    |
| `failed`      | `failed`        | Twilio rejected the message  |

### Phone Number Formatting

All recipient phone numbers must be in E.164 format. The engine validates and normalizes common formats before sending:

```
+15551234567        -> valid, sent as-is
(555) 123-4567      -> normalized to +15551234567 (assumes US)
555-123-4567        -> normalized to +15551234567 (assumes US)
+44 20 7946 0958    -> valid, sent as-is
```

### SMS Best Practices
- Keep messages under 160 characters to avoid multi-segment charges
- Include opt-out instructions ("Reply STOP to unsubscribe") for non-transactional messages
- Use the `messaging_service_sid` for automatic number selection and 10DLC compliance
- Implement rate limiting (default: 1 SMS per user per 60 seconds for MFA codes)
- Do not include shortened URLs from public shorteners (carrier filtering risk)

---

## Push Notifications -- Firebase Cloud Messaging (FCM)

Push notifications are delivered through FCM to Android, iOS, and web clients. FCM handles both platforms via a unified API; APNs delivery is proxied through FCM's built-in iOS support.

### FCM Configuration

```yaml
push:
  provider: fcm
  fcm:
    project_id: "acme-platform-prod"
    service_account_key_path: "/secrets/fcm-service-account.json"
    default_ttl: 86400              # Seconds, how long FCM retains undelivered messages
    android:
      priority: "high"
      notification_channel_id: "acme_default"
    apns:
      headers:
        apns-priority: "10"
        apns-push-type: "alert"
      payload:
        aps:
          sound: "default"
    webpush:
      headers:
        urgency: "high"
```

### Device Token Management

Device tokens are stored in the User Preferences service and linked to user accounts. The engine manages token lifecycle automatically:

- **Registration**: Client apps register tokens on login and app launch
- **Refresh**: Tokens are updated when FCM issues a new canonical token
- **Cleanup**: Stale tokens are removed when FCM returns `UNREGISTERED` or `INVALID_ARGUMENT`

```bash
# Register a device token
curl -X POST https://notifications.acme-platform.io/v1/devices \
  -H "Authorization: Bearer <user_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "ios",
    "token": "fcm_device_token_abc123",
    "app_version": "4.2.1"
  }'
```

### Push Payload Structure

```json
{
  "notification": {
    "title": "Order Shipped",
    "body": "Your order ORD-20261013-001 is on its way!",
    "image": "https://cdn.acme-platform.io/images/shipping-icon.png"
  },
  "data": {
    "action_url": "acme://orders/ORD-20261013-001",
    "notification_id": "ntf_r4s5t6u7v8w9"
  }
}
```

### Push Best Practices
- Use collapse keys to prevent notification floods (e.g., only show the latest chat message)
- Respect platform payload limits: 4KB for both FCM and APNs
- Use data-only (silent) push for background data sync without user-visible notifications
- Set appropriate TTL values: short for time-sensitive alerts, longer for informational updates

---

## Webhook Configuration

Webhooks allow external systems to receive notification delivery events via HTTPS callbacks.

### Registering a Webhook Endpoint

```bash
curl -X POST https://notifications.acme-platform.io/v1/webhooks \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://partner-system.example.com/acme-events",
    "events": ["notification.sent", "notification.delivered", "notification.failed", "notification.bounced"],
    "secret": "whsec_a1b2c3d4e5f6g7h8i9j0",
    "active": true,
    "description": "Partner delivery tracking integration"
  }'
```

### Webhook Payload

Each delivery includes an HMAC-SHA256 signature in the `X-Acme-Signature-256` header for verification:

```json
{
  "event": "notification.delivered",
  "notification_id": "ntf_r4s5t6u7v8w9",
  "channel": "email",
  "recipient": "jdoe@example.com",
  "provider": "sendgrid",
  "timestamp": "2026-10-13T14:30:04Z",
  "metadata": {
    "provider_message_id": "sg_abc123def456",
    "template_slug": "password-reset"
  }
}
```

### Signature Verification

```python
import hmac
import hashlib

def verify_webhook(payload_bytes: bytes, signature_header: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)
```

### Webhook Best Practices
- Always verify the `X-Acme-Signature-256` header before processing payloads
- Respond with 2xx within 10 seconds; the engine treats timeouts as failures
- Implement idempotency using the `notification_id` to handle retries gracefully
- Webhooks follow the standard retry policy (5 retries with exponential backoff)
- After 5 consecutive failures, the endpoint is automatically disabled and the tenant admin is notified

---

## Channel Priority and Fallback

When a notification specifies multiple channels, the engine delivers to all of them in parallel by default. Alternatively, configure **priority-based fallback** to try channels sequentially.

```json
{
  "template": "critical-alert",
  "recipient": { "user_id": "usr_8f3a2c91e4b7" },
  "channel_strategy": "fallback",
  "channels": ["push", "sms", "email"],
  "fallback_timeout_ms": 30000
}
```

In this example:
1. The engine attempts push delivery first
2. If push fails or goes undelivered after 30 seconds, it falls back to SMS
3. If SMS also fails, it falls back to email

### Strategy Options

| Strategy    | Behavior                                                      |
|-------------|---------------------------------------------------------------|
| `parallel`  | Deliver to all specified channels simultaneously (default)    |
| `fallback`  | Try channels in order; proceed to next on failure or timeout  |
| `preferred` | Deliver to the user's preferred channel only                  |

---

## Full Channel Configuration File

Below is a complete `channels.yaml` example for production:

```yaml
channels:
  email:
    provider: sendgrid
    sendgrid:
      api_key: "${SENDGRID_API_KEY}"
      from_address: "noreply@acme-platform.io"
      from_name: "Acme Platform"
      reply_to: "support@acme-platform.io"
      ip_pool: "transactional"
      tracking:
        click_tracking: true
        open_tracking: true
    rate_limit: 100/s

  sms:
    provider: twilio
    twilio:
      account_sid: "${TWILIO_ACCOUNT_SID}"
      auth_token: "${TWILIO_AUTH_TOKEN}"
      messaging_service_sid: "MG9a1b2c3d4e5f6a7b8c9d0e1f2a3b4c"
      status_callback_url: "https://notifications.acme-platform.io/webhooks/twilio/status"
    rate_limit: 10/s

  push:
    provider: fcm
    fcm:
      project_id: "acme-platform-prod"
      service_account_key_path: "/secrets/fcm-service-account.json"
      default_ttl: 86400
    rate_limit: 500/s

  webhook:
    timeout_ms: 10000
    max_payload_bytes: 65536
    signing_algorithm: hmac-sha256
    retry_max: 5
    rate_limit: 50/s
```

---

## Related Documentation

- [Notification Engine README](README.md) -- full service overview and API reference
- [Auth Service](../auth-service/README.md) -- authentication for service tokens
- [Event Bus](../event-bus/README.md) -- Kafka topic configuration
