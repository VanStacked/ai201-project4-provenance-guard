# Provenance Guard

A backend API that classifies creative text content as AI-generated or human-written, returns a confidence score, displays a transparency label, and handles creator appeals.

---

## Architecture Overview

A submitted piece of text flows through two independent detection signals — an LLM classifier and a stylometric heuristics analyzer. Their scores are combined into a single confidence score. That score maps to a transparency label. Everything is logged to an append-only JSONL audit log and returned to the caller.

**Submission flow:**

    POST /submit
         │
         ▼
    ┌─────────────┐     ┌──────────────────────┐
    │  LLM Signal │     │ Stylometric Signal   │
    │  (Groq)     │     │ (sentence variance,  │
    │  score: 0-1 │     │  TTR, punctuation)   │
    │  weight: 65%│     │  score: 0-1          │
    └──────┬──────┘     │  weight: 35%         │
           │            └──────────┬───────────┘
           └──────────┬────────────┘
                      ▼
             Confidence Scoring
             (weighted average)
                      │
                      ▼
             Transparency Label
             (high_ai / uncertain / high_human)
                      │
                      ▼
               Audit Log (JSONL)
                      │
                      ▼
                JSON Response

**Appeal flow:**

    POST /appeal
         │
         ▼
    Find entry by content_id
         │
         ▼
    Update status to "under_review"
    Add appeal_reasoning to log
         │
         ▼
    JSON Response

---

## Detection Signals

### Signal 1: LLM-based classification (Groq)
- **Measures:** Whether the text reads as AI-generated based on phrasing, sentence structure, and voice — captured holistically by the model
- **Why:** AI text tends to use filler phrases, uniform structure, and generic language that a language model can recognize
- **Blind spot:** Lightly edited AI output and formal human writing styles can fool it

### Signal 2: Stylometric heuristics
- **Measures:** Three statistical properties — sentence length variance, type-token ratio (vocabulary diversity), and punctuation density
- **Why:** AI text is statistically more uniform than human text; humans vary sentence length, reuse fewer words proportionally, and use more varied punctuation
- **Blind spot:** Academic and legal writing is structurally uniform and scores as AI-like even when human-written

### Signal combination
LLM signal is weighted 65%, stylometric signal 35%. The LLM signal carries more weight because it captures semantic properties that heuristics miss.

---

## Confidence Scoring

| Score range | Attribution | Label variant |
|---|---|---|
| >= 0.75 | likely_ai | High-confidence AI |
| 0.36 to 0.74 | uncertain | Uncertain |
| <= 0.35 | likely_human | High-confidence human |

**Example 1 — AI-generated text:**

Input: "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits are numerous, stakeholders must collaborate to ensure responsible deployment."

- LLM score: 0.8
- Stylometric score: 0.453
- Combined confidence: 0.679
- Label: Uncertain

**Example 2 — Human-written text:**

Input: "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after."

- LLM score: 0.1
- Stylometric score: 0.347
- Combined confidence: 0.186
- Label: Likely Human-Written

---

## Transparency Label Variants

**High-confidence AI (confidence >= 0.75):**
"Our system found strong indicators that this content was AI-generated. This does not mean the work has no value, but readers should be aware it may not reflect the creator's personal voice."

**Uncertain (confidence 0.36 to 0.74):**
"Our system could not confidently determine whether this content was AI-generated or human-written. The creator may appeal this classification if they believe it is inaccurate."

**Likely Human-Written (confidence <= 0.35):**
"Our system found strong indicators that this content was written by a human. Natural variation in style, voice, and structure suggest authentic human authorship."

---

## API Endpoints

### POST /submit
Accepts a piece of text for attribution analysis.

Request:
```json
{
  "text": "your content here",
  "creator_id": "user-123"
}
```

Response:
```json
{
  "content_id": "uuid",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.679,
  "llm_score": 0.8,
  "stylo_score": 0.453,
  "label": {
    "verdict": "Uncertain",
    "text": "Our system could not confidently determine...",
    "level": "uncertain"
  }
}
```

### POST /appeal
Contest a classification.

Request:
```json
{
  "content_id": "uuid",
  "creator_reasoning": "I wrote this myself..."
}
```

Response:
```json
{
  "content_id": "uuid",
  "message": "Appeal received. Your content has been marked as under review.",
  "status": "under_review"
}
```

### GET /log
Returns the 20 most recent audit log entries.

---

## Rate Limiting

- **Limit:** 10 requests per minute, 100 requests per day per IP
- **Reasoning:** A real creator submits work occasionally, not dozens of times per minute. 10/minute allows normal use while blocking automated flooding. 100/day is generous for any single legitimate user.

**Rate limit test results (12 rapid requests):**

    200
    200
    200
    200
    200
    200
    200
    200
    200
    200
    429
    429

---

## Audit Log

Every submission and appeal is logged to `audit_log.jsonl`. Sample entries:

    {"content_id": "d524e57b-676c-4a17-967c-97e97cb58ca9", "creator_id": "test-user-1", "timestamp": "2026-06-29T11:52:39.423066", "attribution": "uncertain", "confidence": 0.679, "llm_score": 0.8, "stylo_score": 0.453, "status": "under_review", "appeal_reasoning": "I wrote this myself. I am a non-native English speaker and my formal writing style may appear AI-generated."}
    {"content_id": "b234a338-4a4e-4916-ba43-73445cc8f340", "creator_id": "test-user-2", "timestamp": "2026-06-29T11:53:00.326554", "attribution": "likely_human", "confidence": 0.186, "llm_score": 0.1, "stylo_score": 0.347, "status": "classified", "appeal_reasoning": null}
    {"content_id": "2b3233e8-e3f7-470f-9fb0-4cb95930f045", "creator_id": "demo-3", "timestamp": "2026-06-29T12:35:35.741800", "attribution": "likely_ai", "confidence": 0.754, "llm_score": 0.8, "stylo_score": 0.67, "status": "classified", "appeal_reasoning": null}

---

## Known Limitations

1. **Formal human writing** — Academic essays, legal briefs, and technical documentation are structurally uniform. The stylometric signal will score these as AI-like, potentially producing false positives for human writers in professional fields.
2. **Very short text** — Fewer than 2 sentences makes sentence length variance meaningless. The system defaults the stylometric score to 0.5, making the LLM signal carry all the weight.

---

## Spec Reflection

**One way the spec helped:** Defining the three confidence thresholds (0.75 / 0.35) in planning.md before coding meant the label generation function had a clear contract to implement against — no guessing at the boundary values during coding.

**One way implementation diverged:** The planning doc assumed the stylometric signal would be the weaker signal. In practice, the LLM signal was far more reliable on casual human text, which is why its weight was increased to 65%.

---

## AI Usage

1. **App skeleton and LLM signal:** Provided the architecture diagram and detection signals spec to Claude. It generated the Flask route structure and the Groq prompt. I revised the prompt to return only a float (the first version returned a paragraph of explanation), and adjusted the score parsing to strip punctuation before casting to float.

2. **Stylometric signal:** Provided the three metrics (sentence variance, TTR, punctuation density) to Claude with the spec. It generated the function. I tested it against formal human writing and found it was over-penalizing academic text, so I adjusted the variance normalization divisor from 100 to 50 to make the signal less aggressive.