---
name: email-marketing-filter
description: Recognize bulk/marketing email and silence triage cycles
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(unsubscribe|view in browser|view this email|update your preferences|no-?reply|do-?not-?reply|newsletter|special offer|limited time|exclusive deal)"
  status: active
  supersedes: null
---

## When to apply
An incoming email that is clearly bulk/marketing, identifiable by an unsubscribe footer, no-reply sender, or marketing copy.

## Procedure
1. Apply label `pm/fyi`.
2. Do NOT draft a reply. Marketing senders don't read replies.
3. Do NOT log a followup. There is nothing to follow up on.
4. Stop. This thread is done.

## Anti-patterns
- Don't apply `pm/urgent` because the marketing copy says "URGENT: 24 HOURS LEFT". That's the sender's framing, not Sol's reality.
- Don't extract tasks from "click here to claim". Not a task.

## Tools this playbook expects
- `gmail_add_label`
