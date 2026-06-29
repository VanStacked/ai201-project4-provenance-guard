# Planning: Provenance Guard

## Architecture Narrative

A piece of text enters the system via POST /submit. It passes through two independent detection signals — an LLM-based classifier and a stylometric heuristics analyzer. Their scores are combined into a single confidence score (weighted 65/35 in favor of the LLM signal). That score maps to a transparency label and an attribution result. Everything is written to an append-only JSONL audit log. The full result is returned to the caller.

If a creator disputes the classification, they submit a POST /appeal with their content_id and reasoning. The system updates the log entry status to "under_review" and returns confirmation.

## Architecture Diagram

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

## Detection Signals

### Signal 1: LLM-based classification (Groq)
- **Measures:** Semantic and stylistic coherence holistically — whether the text reads as AI-generated based on phrasing, structure, and voice
- **Output:** Float 0.0–1.0 (0 = definitely human, 1 = definitely AI)
- **Blind spot:** Cannot reliably detect lightly edited AI output or formal human writing styles

### Signal 2: Stylometric heuristics
- **Measures:** Three statistical properties: sentence length variance, type-token ratio (vocabulary diversity), and punctuation density
- **Output:** Float 0.0–1.0 (average of three sub-scores)
- **Blind spot:** Formal human writing (academic papers, legal documents) scores as AI-like because it is structurally uniform

## Uncertainty Representation

- Confidence >= 0.75 = Likely AI-Generated (high_ai)
- Confidence 0.36 to 0.74 = Uncertain
- Confidence <= 0.35 = Likely Human-Written (high_human)

A score of 0.6 means the system leans toward AI but not confidently — the label says "Uncertain" and the creator is explicitly told they can appeal.

## Transparency Label Variants

**High-confidence AI (confidence >= 0.75):**
"Our system found strong indicators that this content was AI-generated. This does not mean the work has no value, but readers should be aware it may not reflect the creator's personal voice."

**Uncertain (confidence 0.36 to 0.74):**
"Our system could not confidently determine whether this content was AI-generated or human-written. The creator may appeal this classification if they believe it is inaccurate."

**Likely Human-Written (confidence <= 0.35):**
"Our system found strong indicators that this content was written by a human. Natural variation in style, voice, and structure suggest authentic human authorship."

## Appeals Workflow

- Any creator can submit an appeal via POST /appeal
- They provide their content_id and a written reasoning
- The system updates the log entry status to "under_review" and stores the reasoning
- A human reviewer would use GET /log to see all under_review entries
- Automated re-classification is not performed

## Anticipated Edge Cases

1. **Formal human writing** (academic essays, legal briefs) — high sentence uniformity and low punctuation density will push the stylometric score toward AI-like, potentially causing false positives
2. **Very short text** (one or two sentences) — sentence length variance cannot be computed meaningfully with fewer than 2 sentences; the system defaults to 0.5 for stylometrics

## Rate Limiting

- 10 requests per minute, 100 per day per IP
- Reasoning: a real creator submits work occasionally, not dozens of times per minute. 10/minute allows normal use while blocking automated flooding. 100/day is generous for legitimate use.

## AI Tool Plan

### M3 (submission endpoint + first signal)
- Provided to AI: detection signals section + architecture diagram
- Asked for: Flask app skeleton + LLM signal function
- Verification: called the function directly with AI and human text samples

### M4 (second signal + confidence scoring)
- Provided to AI: detection signals + uncertainty representation + diagram
- Asked for: stylometric signal function + weighted scoring logic
- Verification: checked that scores varied meaningfully between clearly AI and clearly human text

### M5 (production layer)
- Provided to AI: label variants + appeals workflow + diagram
- Asked for: label generation function + /appeal endpoint
- Verification: tested all three label variants are reachable, confirmed appeal updates status in log