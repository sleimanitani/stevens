---
name: email-sensitive-escalation
description: Identify legal/financial/medical/security-sensitive threads and stop drafting
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(lawsuit|attorney|legal|subpoena|tax (notice|audit)|irs|hipaa|breach|incident|password|2fa code|verification code|wire transfer|account number|ssn|social security)"
  status: active
  supersedes: null
---

## When to apply
An incoming email that is plausibly legal, financial, medical, or security-sensitive — anything where a wrong-tone autoreply could cause real harm.

## Procedure
1. Apply label `pm/sensitive`.
2. Do NOT draft a reply. This is a hard stop.
3. Log a followup `direction=waiting_on_me`, `deadline=24h`, note="SENSITIVE — Sol must read and reply".
4. Do not summarize or quote the contents in the followup note beyond "sensitive thread re: <one-noun>". The followup table is queryable; we don't want sensitive content there.

## Anti-patterns
- Do not draft an "I've passed this along" reply. That's still a reply, and the wrong sender will think they've been heard.
- Do not extract tasks via the `task_extraction` playbook even if asks are present — sensitive overrides everything.

## Tools this playbook expects
- `gmail_add_label`
- `log_followup`
