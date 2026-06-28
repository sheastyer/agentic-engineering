# Org run — meal-planner

- **Workflow:** `feedback-demo-34517ba6`
- **Generated:** 2026-06-28 15:55:08

## Outcome

- **Status:** shipped
- **Cost:** $0.973976  (30159 tokens)
- **Summary:** Shipped on branch agentic/feat-add-a-guardrail-to-the-chat-agen-34517ba6.
- **Product PR:** https://github.com/sheastyer/meal-planner/pull/13 (opened=True)
- **Stage log:**
  1. pm_draft_brief
  2. exec_council
  3. council: human override -> approved (agents advisory)
  4. pm_write_prd
  5. architect_review_prd[pass 1]
  6. pm_revise_prd
  7. architect_review_prd[pass 2]
  8. PRD approved by architect on pass 2
  9. ux_generate_mocks
  10. consumer_research
  11. pm_signoff
  12. architect_plan_stories
  13. engineering_pod
  14. deploy_approval
  15. deploy

## Brief

- **Problem:** The meal-planner chat agent currently engages with arbitrary off-topic requests. This dilutes the product's purpose, wastes model tokens, and risks the assistant behaving as a general-purpose chatbot. There's no scope-limiting behavior to keep conversations focused on planning the household's week of meals.
- **Summary:** Add a guardrail to the chat agent so it stays on-topic: when a user message is unrelated to meal planning (e.g. "write a Java hello world"), the agent politely declines and redirects back to meal planning instead of attempting to answer.
- **UI-impacting:** True

## Council votes

| Voter | Approve | Rationale |
| --- | --- | --- |
| legal | True | Scope-limiting a chat agent to its intended domain raises no concrete legal or compliance concerns — there is no handling of sensitive personal data, no regulated health claims, and no IP or licensing risk introduced by declining off-topic messages. The feature actually reduces risk by limiting the assistant's exposure to harmful or legally fraught general-purpose use cases. |
| sales | True | Scope guardrails sharpen the product's identity as a dedicated meal-planning tool, reducing token waste and preventing the assistant from drifting into general-purpose chatbot territory that would undermine differentiation and operator trust. Keeping users focused on meal planning increases the probability of completing a weekly plan — the core retention loop — rather than getting distracted by off-topic tangents. |

## PRD & architect iterations

- **PRD v1** — see `prd.md`
- **PRD v2** — see `prd.md`
- **Architect pass 1** — approved=False: ["The PRD body is truncated — it ends mid-sentence ('A user can type') and contains no requirements, acceptance criteria, technical specification, or scope definition. There is nothing substantive to evaluate or break into stories."]
- **Architect pass 2** — approved=True: []

## Consumer research

- **Overall sentiment:** positive
- **time-constrained professional** (positive): Makes sense to me — I open this app to get dinner sorted fast, not to have the AI wander off into random topics. As long as it doesn't start flagging my quick 'what's easy with leftover rotisserie chicken?' messages as off-topic, I'm all for it keeping things focused. Just don't make the redirect message so wordy that it slows me down when I accidentally type something vague.
- **budget-conscious** (positive): Honestly, anything that keeps this focused on what I'm actually paying for — meal planning — is a win for me. I don't want the service burning through compute (and potentially driving up costs) because someone asked it about the stock market. Just make sure it doesn't get too trigger-happy and block me when I'm asking something like 'what's a cheap protein source this week?'
- **first-time user** (positive): As someone just starting out, I was honestly worried I'd accidentally ask something dumb and break the whole thing — so knowing it'll just politely redirect me instead of spitting out some weird error is reassuring. I like that it'll tell me what it *can* help with, because I'm still figuring out what this app even does. My only concern is whether it might block me if I ask something like 'is this ingredient healthy?' — hope it's not too strict about what counts as on-topic.
- **power user** (neutral): I get why you'd want to keep the agent focused, but as a power user I'm already using this tool exclusively for meal planning — this guardrail does nothing for me and just adds a classification hop I have to pay for in latency. The 'borderline messages' decision worries me too: if I ask something like 'what's the history of miso fermentation' to understand an ingredient better, I don't want to hit a canned rejection. Net-net it's fine infrastructure work, just not something I'd notice or care about.

## Story plan

- **Complexity:** medium
  - feat-add-a-guardrail-to-the-chat-agen-S1 Add guardrail constants file with classification prompt, allowed-topic list, and canned off-topic response message
  - feat-add-a-guardrail-to-the-chat-agen-S2 Implement classifier function in lib/guardrail with LLM call, 500ms timeout, and fail-open error handling
  - feat-add-a-guardrail-to-the-chat-agen-S3 Wire guardrail classifier into app/api/chat/route.ts and return canned response for OFF_TOPIC messages

## Engineering pod

- **feat-add-a-guardrail-to-the-chat-agen** — status=failed, cost=$0.5319361: Everything looks correct. Here's a summary of what was implemented across the three stories:

---

### What was built

**Story 1 — `lib/guardrail/constants.ts`**
- `ALLOWED_TOPICS`: typed `as const` array of 13 meal-planning subjects used in the prompt
- `CLASSIFICATION_PROMPT`: system prompt that instructs the LLM to reply with exactly `ON_TOPIC` or `OFF_TOPIC`
- `OFF_TOPIC_RESPONSE`: the friendly canned reply returned to users when their message is off-topic

**Story 2 — `lib/guardrail/index.t
- **QA:** passed=False — (stub) failing stories

## Deploy

- deployed=True, ref=agentic/feat-add-a-guardrail-to-the-chat-agen-34517ba6
