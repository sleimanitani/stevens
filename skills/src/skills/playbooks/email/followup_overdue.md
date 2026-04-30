---
name: email-followup-overdue
description: Surface threads where the other party owes us a response past deadline
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(following up|circling back|any update|gentle reminder|bump|haven't heard)"
  status: active
  supersedes: null
---

## When to apply
An incoming email that is itself a followup on a thread, OR the daily review surfaces a thread tagged `pm/waiting-on-them` whose deadline has passed.

## Procedure
1. Call `gmail_get_thread` and read the latest exchange.
2. If the other party is the one nudging US (i.e., we owe them): apply `pm/waiting-on-me`, draft a brief response acknowledging and stating when we'll have an answer, log a followup with `direction=waiting_on_me`.
3. If we were the ones waiting on them: apply `pm/urgent` and flag for Sol — drafting another nudge is Sol's call, not ours.

## Anti-patterns
- Do not auto-draft a "sorry for the delay" reply without reading the thread. Could be a context the user-facing apology shouldn't be sent under.
- Do not mark the followup `done` automatically — only Sol marks done.

## Tools this playbook expects
- `gmail_get_thread`
- `gmail_create_draft`
- `gmail_add_label`
- `log_followup`
