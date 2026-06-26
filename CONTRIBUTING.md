# Team Workflow: - WhatsApp Recruiting Pipeline

This document explains how we work on the project together. Your job is to write code that fits into our existing setup. You don't need to run or test the whole project on your own.
---

## 1. Branching & PRs

- **One branch per task** (not per person) — branch off `main` for each individual task/issue you're assigned
- Branch naming: `yourname/short-task-description` (e.g. `Nanditha/excel-validation`, `Sanjan/rule-engine`,`Aditya/webhook-idempotency`)
- Push small, frequent commits — don't wait until a whole milestone is "done" to push
- Open a PR as soon as a logical chunk is ready
- Every PR should include `pytest` tests for the logic you wrote, where testable (see section 3)

---

## 2. Config — never hardcode connection details

Copy `.env.example` to your own `.env` file. Always read credentials via `os.environ` — never hardcode a real value in a `.py` file. Never commit your real `.env` file.

---

## 3. What you CAN test on your own laptop

| Person | What's testable standalone |
|---|---|
| **Nanditha** | pandas parsing/validation/dedup logic |
| **Sanjan** | Rule engine (opt-out, escalation keywords, window check) + LLM prompt output parsing (mock the real API call in tests) |
| **Aditya** | Dashboard UI against mock data; webhook idempotency logic |

Write `pytest` tests for these and run them yourself before opening a PR.

---

## 4. What you CANNOT test on your own laptop

- Anything needing the database, queue, or real Twilio send/receive
- Full end-to-end flow

Write the required code along with unit tests for the testable parts, and then push your changes to GitHub.

---

## 5. Function contracts

- **Nanditha**: DB schema (table/column names, status enum values)
- **Sanjan**: rule-engine gate return shape; LLM JSON output (`reply`, `status`, `confidence`, `escalate`)
- **Aditya**: dashboard query fields
