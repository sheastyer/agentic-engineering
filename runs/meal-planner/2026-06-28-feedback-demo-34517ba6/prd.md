# PRD (v2)

# PRD: Chat Agent On-Topic Guardrail

## Problem & Context

The meal-planner chat agent currently processes and responds to any user message regardless of subject matter. A user can type anything — stock tips, coding questions, general trivia — and the agent will engage, wasting tokens and diluting the product's purpose. This PRD defines a guardrail that keeps the agent focused on meal planning, recipes, nutrition, and household dietary preferences.

---

## Goals

1. Reject off-topic messages gracefully before they reach the underlying LLM planning logic.
2. Give users a clear, friendly explanation of what the agent can help with.
3. Introduce no measurable latency regression for on-topic messages.

## Non-Goals

- Building a general-purpose content moderation system.
- Blocking messages based on tone or sentiment (rudeness, frustration, etc.).
- Changing any existing recipe-retrieval or meal-plan generation logic.

---

## Scope Definition

**In scope**
- A classification step inserted into the chat message-handling pipeline.
- A configurable list of allowed topic categories (meal planning, recipes, ingredients, nutrition, dietary restrictions, household preferences).
- A canned off-topic response with a suggestion to redirect the conversation.
- Server-side enforcement (the guardrail runs in the API route, not only in the browser).

**Out of scope**
- User-facing topic-category configuration.
- Admin UI for managing allowed topics.
- Logging or analytics on off-topic rates (may be addressed in a follow-up).

---

## Technical Specification

### 1. Classification step

- Location: `app/api/chat/route.ts`, before the message is forwarded to the planning agent.
- Implementation: a lightweight prompt sent to the same LLM used by the planning agent, asking it to classify the user message as `ON_TOPIC` or `OFF_TOPIC` given a system prompt that enumerates the allowed topic categories.
- The classification call must complete within **500 ms** (p95). If it times out or errors, the message is allowed through (fail-open) to avoid blocking legitimate users.
- The classification prompt and allowed-topic list are stored in a single constant file (`lib/guardrail/topics.ts`) so they can be updated without touching route logic.

### 2. Response for off-topic messages

- HTTP status: `200` (keeps the chat UI's error handling simple).
- Response body: standard chat message shape already used by the app, with `role: 'assistant'` and a fixed message string defined in `lib/guardrail/topics.ts`.
- The fixed message must:
  - Acknowledge the user's message without repeating it.
  - State clearly that the agent is focused on meal planning.
  - Offer one concrete example of something the agent can help with.

### 3. Data layer

- No schema changes required. The guardrail is stateless; nothing is persisted.

### 4. Environment

- No new environment variables. The classification call reuses the existing LLM client and API key already configured for the planning agent.

---

## Acceptance Criteria

| # | Criterion | How to verify |
|---|-----------|---------------|
| 1 | An on-topic message (e.g., "What can I make with chicken and spinach?") reaches the planning agent and returns a recipe suggestion. | Manual test + existing integration tests pass. |
| 2 | An off-topic message (e.g., "What is the capital of France?") never reaches the planning agent and returns the canned assistant response. | Unit test mocking the LLM classifier. |
| 3 | If the classifier call throws or times out, the message is forwarded to the planning agent unchanged. | Unit test simulating classifier timeout/error. |
| 4 | The canned off-topic response is a valid message in the existing chat message shape (TypeScript type check passes). | CI type-check step. |
| 5 | p95 latency for on-topic messages does not increase by more than 100 ms relative to the pre-guardrail baseline, measured in a load test against the staging environment. | Load test in staging before merge. |

---

## User Stories

**US-1 — On-topic flow (happy path)**
As a household member using the meal planner, when I send a message about meals or recipes, I receive a helpful response from the planning agent so that I can plan my week effectively.

**US-2 — Off-topic message**
As a household member, when I send a message unrelated to meal planning, I receive a clear, friendly message explaining what the agent can help with, so that I am not confused by silence or a generic error.

**US-3 — Classifier failure**
As a household member, when the guardrail classifier is unavailable, my message is still processed by the planning agent so that a backend hiccup does not block my meal-planning session.

---

## Open Questions

1. Should borderline messages (e.g., general food culture questions not tied to meal planning) be treated as on-topic or off-topic? **Default decision: treat as on-topic** to minimise false positives; revisit after observing real traffic.
2. Should the off-topic rate be surfaced in any existing monitoring dashboard? Deferred to a follow-up ticket.

---

## Out-of-Scope Follow-ups

- Analytics/logging of off-topic message frequency.
- A/B testing alternative guardrail copy.
- Fine-tuning or replacing the LLM classifier with a cheaper embedding-based classifier if token costs become significant.


---

## Revision history

### v1

# PRD: Chat Agent On-Topic Guardrail

## Problem & Context

The meal-planner chat agent currently processes and responds to any user message regardless of subject matter. A user can type 
