---
name: email-appointment-request
description: Triage incoming meeting/call/scheduling requests on email
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(meeting|call|schedule|available|calendly|book a time|when works)"
  status: active
  supersedes: null
---

## When to apply
An incoming email asking to schedule a meeting, call, or appointment.

## Procedure
1. If the sender included a Calendly link, draft a short acknowledgment ("thanks, I'll book a slot") and apply label `pm/appointment-pending`.
2. Otherwise, draft a reply asking for 2–3 time windows they prefer.
3. Apply label `pm/appointment-pending`.
4. Log a followup with `direction=waiting_on_me`, `deadline=2 business days`.

## Variants
- Urgent language ("ASAP", "today"): also apply `pm/urgent`.
- Recurring meeting: do NOT draft. Flag for Sol via `log_followup` with note "recurring — Sol decides".

## Anti-patterns
- Do not suggest specific times. Sol's calendar isn't integrated yet for two-way booking.
- Do not commit to a day/time ("tomorrow works"). Only ask for preferences.

## Tools this playbook expects
- `gmail_create_draft`
- `gmail_add_label`
- `log_followup`
