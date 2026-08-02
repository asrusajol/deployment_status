---
name: deployment-request-intake
description: Parses a free-form deployment request message from a developer (e.g. "Client Name: CRM, Server: CRM Live, Version: V12, Changes: ..., Instruction: deploy tomorrow before 11:30am") into structured, validated fields matching the DeploymentRequest schema in project_plan.md, and loudly flags what's missing — especially Task ID and requester name, the two things this team keeps losing track of. Use this any time a developer pastes, forwards, or describes a deployment request, or asks to log/register/check/validate one, even if they don't ask for "JSON" or "structured data" by name.
---

# Deployment Request Intake

## Why this exists

This team's core problem isn't parsing — it's that requests arrive as loose chat/email text, so the Task ID gets dropped, the requester isn't always named, and there's no record of who approved what before a deployment happened. This skill is the stopgap for Phase 0/1 of `project_plan.md`, before the FastAPI app exists: it turns messy text into the same structured record the future app will store, and — more importantly — it forces the missing-Task-ID problem into the open immediately, instead of letting it surface only after a DevOps engineer has already started work.

Treat this as a validation gate, not just a formatter. A "successful" parse of a request with no Task ID is still a failure state that needs to be called out clearly.

## What to extract

From the raw text, pull out:

| Field | Notes |
|---|---|
| `task_id` | Only if explicitly present in the text (e.g. "Task ID: XYZ-123", "Ticket #456"). Never invent one. |
| `client_name` | e.g. "CRM" |
| `server` | e.g. "CRM Live" |
| `version` | e.g. "V12" |
| `changes_description` | Everything under "Changes"/"Module"/similar — keep it as written, don't paraphrase away specifics |
| `requested_by` | Only if a name/email is in the text or was given separately by the user in conversation. Don't guess from context. |
| `deadline_at` | Resolve relative phrases ("tomorrow", "EOD", "before 11:30am") against **today's date**, which you can find in your session context. State the resolved absolute date/time explicitly so there's no ambiguity later (e.g. "tomorrow" resolved against 2026-07-28 → 2026-07-29). |
| `approval_status` | Almost always absent from the raw text itself — that's expected. Report it as `not_recorded` rather than guessing, and note separately (see below) that no approval workflow has happened yet. |

If the raw text was provided as a file, also read it with the Read tool first; if pasted inline, work from the message directly.

## Output format

Always produce two things, in this order:

1. **A missing-fields summary in plain language**, before the JSON. Lead with this — it's the actionable part. If `task_id` is missing, say explicitly that the request **cannot proceed** and should be bounced back to the developer to attach a Task ID, per the team's process. If `requested_by` is missing, flag it too, since unattributed requests are exactly what causes deployments to get executed under the wrong person's request.

2. **A JSON block** matching the `DeploymentRequest` shape from `project_plan.md`, with `status: "pending_intake"` and any unknown field set to `null`. Always include `raw_text` verbatim so nothing is lost even if parsing missed something:

```json
{
  "task_id": null,
  "client_name": "string or null",
  "server": "string or null",
  "version": "string or null",
  "changes_description": "string or null",
  "requested_by": "string or null",
  "deadline_at": "ISO 8601 or null",
  "approval_status": "not_recorded",
  "status": "pending_intake",
  "raw_text": "the original message, unmodified"
}
```

This JSON is meant to be a stopgap seed record — once the FastAPI app from `project_plan.md` exists, this is the payload shape its intake endpoint should accept.

## Worked example 1 — the common case (missing Task ID)

**Input** (received 2026-07-28):
```
Client Name: CRM
Server: CRM Live
Version: V12
Changes:
ProVisu full Module
Instruction: Please execute this deployment tomorrow (28-Jul) morning before at 11:30am.
```

**Expected response:**

> ⚠️ This request is missing a **Task ID** — per your process, it can't be scheduled until the developer attaches one. It's also missing the **requester's name**, so there's no one to follow up with directly.
>
> Note the date in the message ("tomorrow (28-Jul)") is internally inconsistent with today's date (2026-07-28) — tomorrow would be 2026-07-29, not 28-Jul. Flag this back to the developer rather than guessing which one they meant.

```json
{
  "task_id": null,
  "client_name": "CRM",
  "server": "CRM Live",
  "version": "V12",
  "changes_description": "ProVisu full Module",
  "requested_by": null,
  "deadline_at": null,
  "approval_status": "not_recorded",
  "status": "pending_intake",
  "raw_text": "Client Name: CRM\nServer: CRM Live\nVersion: V12\nChanges:\nProVisu full Module\nInstruction: Please execute this deployment tomorrow (28-Jul) morning before at 11:30am."
}
```

(`deadline_at` is left `null` here rather than guessed, because the message's own date reference is ambiguous — see the flag above. When the wording is unambiguous, e.g. "tomorrow" with no conflicting date, resolve it and fill this field in.)

## Worked example 2 — a cleaner request with everything present

**Input** (received 2026-07-28):
```
Task ID: DEP-4521
Requested by: Farhan Ahmed
Client Name: NorthStar Retail
Server: NorthStar Prod
Version: V3.2
Changes: Fix checkout timeout bug, add new payment gateway config
Instruction: Deploy tonight after 9pm, before market open tomorrow.
```

**Expected response:**

> All required identifying fields are present (Task ID, requester, client). Approval isn't recorded in the message itself — confirm a team lead has signed off before this moves to execution, since this skill only validates intake, not approval.

```json
{
  "task_id": "DEP-4521",
  "client_name": "NorthStar Retail",
  "server": "NorthStar Prod",
  "version": "V3.2",
  "changes_description": "Fix checkout timeout bug, add new payment gateway config",
  "requested_by": "Farhan Ahmed",
  "deadline_at": "2026-07-29T09:00:00",
  "approval_status": "not_recorded",
  "status": "pending_intake",
  "raw_text": "Task ID: DEP-4521\nRequested by: Farhan Ahmed\nClient Name: NorthStar Retail\nServer: NorthStar Prod\nVersion: V3.2\nChanges: Fix checkout timeout bug, add new payment gateway config\nInstruction: Deploy tonight after 9pm, before market open tomorrow."
}
```

Here "before market open tomorrow" is unambiguous enough to resolve — deadline is set to the next day's market open, approximated as 9:00 AM local time, since no market-open time was specified. When you have to approximate like this, say so in your response rather than presenting it as a fact.

## Edge cases worth handling deliberately

- **Multiple deployments in one message**: split into separate JSON records, each validated independently — don't merge distinct client/version pairs into one record.
- **Vague deadlines** ("soon", "when you get a chance"): leave `deadline_at` null and say so, rather than inventing a time.
- **Task ID present but the wrong format** for this org's ticketing convention: still extract it as given; this skill isn't the place to validate ticket-ID format against the real system — that's the future API integration's job (Phase 3 of `project_plan.md`).
