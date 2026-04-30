---
name: email-blocker-triage
description: Identify and escalate threads where Sol is blocking someone else
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(blocking|blocked on|waiting for you|need your sign-?off|need your decision|approval needed|stuck without)"
  status: active
  supersedes: null
---

## When to apply
An incoming email indicating that the sender is BLOCKED on Sol's input, decision, or approval.

## Procedure
1. Apply label `pm/urgent`.
2. Draft a short acknowledgment: "Saw this; will respond by EOD <date>." (Use today's date + 1 business day in the agent's timezone.)
3. Log a followup `direction=waiting_on_me`, `deadline=tomorrow EOD`, note=brief one-line summary of what Sol owes.

## Anti-patterns
- Do not commit to a substantive answer in the draft. The point is acknowledgment + a deadline, not the actual decision.
- Do not delegate to another channel ("text me instead") — keep the response on the same thread.

## Tools this playbook expects
- `gmail_create_draft`
- `gmail_add_label`
- `log_followup`
