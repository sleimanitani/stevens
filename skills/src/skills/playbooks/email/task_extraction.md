---
name: email-task-extraction
description: Identify explicit asks/tasks in an email and log them as followups
version: 1.0.0
author: email_pm
license: proprietary
metadata:
  applies_to_topics: ["email.received.*"]
  applies_to_agents: ["email_pm"]
  triggers:
    - regex: "(?i)(can you|could you|please|need you to|action item|todo|by (mon|tue|wed|thu|fri|monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
  status: active
  supersedes: null
---

## When to apply
An incoming email that contains one or more explicit asks of Sol (questions, action items, deliverables with implicit or explicit deadlines).

## Procedure
1. List each distinct ask in the thread (don't lump them — a thread with 3 asks logs 3 followups).
2. For each ask: log a followup with `direction=waiting_on_me`, `deadline` = the explicit deadline if stated, otherwise `+3 business days`.
3. Apply label `pm/waiting-on-me`.
4. Draft NO reply — extracting tasks is silent. Replies happen via other playbooks.

## Anti-patterns
- Do not infer tasks that weren't explicitly asked. "Hope you're well" is not a task.
- Do not collapse multiple asks into one followup. Sol can only mark them done individually.

## Tools this playbook expects
- `log_followup`
- `gmail_add_label`
