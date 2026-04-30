-- 004_skill_proposals.sql
--
-- Tracks every agent-proposed skill (tool or playbook) awaiting Sol's review.
-- The agent does not get to use its own proposal — it goes here and waits.
-- See `CLAUDE_skills_layer.md` for the propose → review → promote flow.
--
-- Status lifecycle:
--   pending  → approved | rejected | superseded
-- Only Sol (via scripts/review_skills.py) advances status away from pending.

CREATE TABLE IF NOT EXISTS skill_proposals (
    proposal_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               TEXT NOT NULL CHECK (kind IN ('tool', 'playbook')),
    proposed_id        TEXT NOT NULL,                  -- e.g. "pdf.read_pdf_v2" or "email/blocker_triage"
    proposing_agent    TEXT NOT NULL,                  -- caller name from agents.yaml
    body_path          TEXT NOT NULL,                  -- path under skills/proposed/
    rationale          TEXT,
    originating_event  UUID,                            -- FK to events table once that exists
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'approved', 'rejected', 'superseded')),
    reviewed_by        TEXT,
    reviewed_at        TIMESTAMPTZ,
    review_notes       TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS skill_proposals_pending_idx
    ON skill_proposals (status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS skill_proposals_proposed_id_idx
    ON skill_proposals (proposed_id);
