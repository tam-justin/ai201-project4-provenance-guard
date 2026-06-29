# Provenance Guard - README.md

---

## Architecture Overview

### Submission Workflow

```
                    +------------------------------+
                    |         POST /submit         |
                    |    { text, creator_id }      |
                    |  Rate limit: 5/min, 100/day  |
                    +-------------+----------------+
                                  |
                   +--------------+--------------+
                   |                             |
                   v                             v
  +------------------------------+  +------------------------------+
  |          Signal 1            |  |          Signal 2            |
  |       LLM Classifier         |  |   Stylometric Heuristics     |
  |                              |  |                              |
  | - llama-3.3-70b-versatile    |  | - Sentence length variance   |
  | - Semantic meaning & tone    |  | - Vocabulary richness (TTR)  |
  | - Context & authorship cues  |  | - Punctuation density        |
  |                              |  |                              |
  |       score: 0.0–1.0         |  |       score: 0.0–1.0         |
  +---------------+--------------+  +--------------+---------------+
                  |                                |
                  +---------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |       combine_signals()      |
                    |                              |
                    |   Length-adaptive weights:   |
                    | < 150 words: 90% / 10%       |
                    | ≥ 150 words: 60% / 40%       |
                    |      (LLM / stylometric)     |
                    |                              |
                    |   confidence_score: 0.0–1.0  |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |       generate_label()       |
                    |                              |
                    |  0.00–0.25 → High-Confidence |
                    |              Human           |
                    |  0.25–0.80 → Uncertain       |
                    |  0.80–1.00 → High-Confidence |
                    |              AI              |
                    |                              |
                    |  → attribution + label_text  |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |       log_submission()       |
                    |                              |
                    |   Writes to SQLite:          |
                    |   - content row              |
                    |   - audit_log row            |
                    |     (event: "submission")    |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |          Response            |
                    |                              |
                    |  content_id, attribution,    |
                    |  label_text, confidence,     |
                    |  llm_score, stylometric_score|
                    +------------------------------+
```

### Appeal Workflow

```
                    +------------------------------+
                    |        POST /appeal          |
                    |  { content_id,               |
                    |    creator_reasoning }        |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |         Validation           |
                    |                              |
                    |  - submission must exist     |
                    |  - attribution must be       |
                    |    High-Confidence AI        |
                    |  - no appeal already         |
                    |    under review              |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |       process_appeal()       |
                    |                              |
                    |  Updates content row:        |
                    |  - status → "Under Review"   |
                    |  - appeal_reason             |
                    |                              |
                    |  Appends to SQLite:          |
                    |  - audit_log row             |
                    |    (event: "appeal")         |
                    +-------------+----------------+
                                  |
                                  v
                    +------------------------------+
                    |           Response           |
                    |                              |
                    |  content_id                  |
                    |  status: "Under Review"      |
                    |  original_confidence_score   |
                    |  original_attribution        |
                    |  original_label              |
                    +------------------------------+
```

---

## Detection Signals

Provenance Guard uses a multi-signal detection approach to classify submitted content. Each signal captures a distinct property of the text (semantic understanding on one side, structural writing patterns on the other), so their failure modes don't overlap. Combining independent signals makes the classifier more robust and harder to fool than any single-signal system.

---

### Signal 1: LLM Classifier

An LLM evaluates the text's semantic meaning, tone, and context and produces a confidence score from **0 to 1**, where 0 indicates likely human-authored and 1 indicates likely AI-generated.

**Why I chose it:** I chose the LLM Classifier as the primary signal because it evaluates the semantic composition of the text (the meaning, intent, and how arguments are built) rather than just flagging surface-level words. An LLM can do what keyword matching can't. An LLM accounts for context. The word "Furthermore" in a student essay carries different weight than the same word in casual conversation, and an LLM can tell the difference. That sensitivity to meaning is what makes it the strongest single signal for AI detection.

**What this signal misses:** It is susceptible to framing. A well-constructed narrative can steer the model toward the wrong conclusion. It also has no visibility into surface-level writing patterns: sentence length distributions, punctuation habits, vocabulary range. These are statistical properties it never directly measures.

---

### Signal 2: Stylometric Heuristics

Stylometric heuristics capture the writing's structural patterns by measuring statistical properties of the text and combining them into a confidence score from **0 to 1**.

The following three heuristics calculate the confidence score:

- **Sentence length variance:** captures how uniform or varied the sentence lengths are across the text.
- **Vocabulary richness (type-token ratio):** captures the ratio of unique words to total words.
- **Punctuation density:** captures the frequency of formal punctuation marks (commas, semicolons, colons) per word.

Each metric produces its own sub-score between 0 and 1. The three sub-scores are equally weighted and averaged (1/3 each) to produce the final stylometric score.

**Why I chose it:** I wanted a second signal that measures something independent from the LLM. These heuristics are purely statistical, so what they measure and their failure modes don't overlap with the LLM's. Each metric also targets a consistent pattern in AI-generated text: sentence lengths tend to be more uniform, vocabulary more repetitive (lower type-token ratio), and formal punctuation tend to be more frequent. Even if the LLM is persuaded by semantically convincing text, the heuristics can still catch those surface-level fingerprints of AI generated text.

**What this signal misses:** The heuristics are unreliable on short texts (fewer than 150 words) where sentence count and vocabulary size are too small to produce stable statistics. They also misfire on structured creative content like poetry, where deliberate formal constraints produce the same surface patterns as AI output, and on writing by non-native English speakers, whose narrower vocabulary and more uniform sentence structures can resemble AI-generated text.

---

## Confidence Scoring

### Combining Both Signals

The two signals are combined via a **length-adaptive weighted average** into a single final confidence score (0 to 1).

A weighted average is appropriate because both signals contribute meaningful, independent information, and the final score should reflect both rather than letting one gate or override the other. The weights are length-adaptive because stylometric heuristics are unreliable on short texts. They are heavily discounted below 150 words and given more influence once the text is long enough to stabilize:

- **< 150 words:** 90% LLM / 10% stylometric
- **≥ 150 words:** 60% LLM / 40% stylometric

The final score feeds directly into the attribution a text receives, which maps it to one of three transparency labels.

---

### Uncertainty Representation

Given the final score, the system maps it to an attribution so the result can be effectively communicated to the user through a transparency label. The three attributions are: `High-Confidence Human`, `Uncertain`, and `High-Confidence AI`. The thresholds are defined and explained below.

---

#### Thresholds & Reasoning

The following thresholds map the raw combined score to an attribution:

- **High-Confidence Human:** `0.00 to 0.25`
- **Uncertain:** `0.25 to 0.80`
- **High-Confidence AI:** `0.80 to 1.00`

There are a few reasons for these thresholds:

1. **Asymmetry:** Falsely flagging human-authored content as AI-generated is more harmful than missing AI-generated content. To reflect this, the thresholds are intentionally asymmetric: the `High-Confidence AI` label requires a stronger signal (score of 0.80 or above) than the `High-Confidence Human` label (0.25 or below). When in doubt, the system defaults to `Uncertain` rather than risk a false accusation.
2. **High-Confidence Human (0.00 to 0.25):** A score in this range means both the LLM classifier and the stylometric heuristics agree the text is likely human-authored. The weighted average can only land this low when both signals are pulling in the same direction.
3. **Uncertain (0.25 to 0.80):** A score in this range indicates the two signals are contradicting each other, or that the text is too short for either signal to produce reliable results. Rather than forcing a verdict, the system surfaces this ambiguity directly to the user.
4. **High-Confidence AI (0.80 to 1.00):** A score in this range means the LLM classifier is confident the text is AI-generated and the stylometric heuristics are in agreement. The high threshold ensures this label is only applied when both signals converge strongly on the same conclusion.

---

### Testing the Confidence Score

In order to validate that the confidence score actually represents something meaningful, I tested the system with test submissions to ensure the model gives varying confidence scores based on the type of text submitted. The tests are outlined and described below.

#### Example 1: AI-Generated Text

**Text Input:**

> Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment.

| LLM Score | Stylometric Score | Combined Confidence Score | Attribution |
| :---: | :---: | :---: | :---: | 
| 0.92 | 0.37 | 0.86 | High-Confidence AI |

---

#### Example 2: Human-Written Text

> ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably won't go back unless someone drags me there

| LLM Score | Stylometric Score | Combined Confidence Score | Attribution |
| :---: | :---: | :---: | :---: | 
| 0.10 | 0.18 | 0.10 | High-Confidence Human |

---

## Transparency Label Design

| Confidence Score Thresholds | Attribution | Transparency Label |
| :---: | :---: | :---: |
| `0.00 <= score < 0.25` | `High-Confidence Human` | "This content was most likely written by a human." |
| `0.25 <= score < 0.80` | `Uncertain` | "We weren't able to determine whether this content was written by a human or generated by AI. This can happen when the content is too short or when our analysis returns mixed results." |
| `0.80 <= score <= 1.00` | `High-Confidence AI` | "This content shows strong signs of being AI-generated." |

---

## Rate Limiting

Rate limiting is applied to `POST /submit`, keyed by the requester's IP address:

- **5 requests per minute**
- **100 requests per day**

Requests that exceed either limit receive a `429 Too Many Requests` response. `POST /appeal` and `GET /log` are not rate limited.

### Limit Value Reasoning

The specific limit values were chosen to be comfortable for genuine human use, while cutting off automated abuse based on the reasoning below.

---

**5 per minute**

A typical human user will not produce multiple artifacts for submission in a mere minute. The usual workflow is: paste a submission, read the result, make edits, and then re-submit. A user's editing process will likely take longer than 1 minute. For example, a five submission per minute limit gives a comfortable buffer for a writer who is doing a rapid paragraph-level iteration without even coming close to hitting the limit. 

For an automated script trying to abuse the API, a burst limit of 5 submissions per minute stops the abuse immediately, and also prevents the LLM's processing queue from being overwhelmed for genuine users.

---

**100 per day**

A genuine human will not typically come close to 100 submissions a day. A prolific writer, student, musician, or artist working through multiple projects might check a dozen artifacts a day, with several revisions each. For example, at 5 revisions across 15 artifacts, the user will top out at around 75 requests. A 100 request per day limit is a generous ceiling for genuine human workloads.

For abuse scenarios, a 100 request limit per day prevents the system from being abused. Even if they are not rapidly hitting the API and overwhelming the processing queue for genuine users, the expected cost from abuse is limited to 100 submissions a day. 

---

## Known Limitations

The following sections cover the known limitations of the current system (specific content types the system would likely misclassify), and why. 

---

### Non-Native English Writers

A user who is a non-native English writer may produce text with more uniform sentence structures, a narrower vocabulary range, and simpler production patterns compared to a fluent native writer. These are the same surface features that the stylometric signal uses to detect AI-generated content, which means this group is at a higher risk of false positives. The LLM classifier may partially offset this issue, if the semantic content reads as clearly human, but the weighted average could still push the score high enough to produce an `Uncertain`, or potentially a `High-Confidence` attribution.

---

### Structured Creative Content

Poetry and other structured creative forms (sonnet, haiku, villanelle, song lyrics) impose deliberate constraints on sentence length, rhythm, and punctuation that have nothing to do with AI-generation. A user who submits a poem with tightly controlled line lengths will have low sentence length variance. In addition, sparse punctuation in verses will produce a flat punctuation density score. These stylometric heuristics will likely misread these as an AI signal. 

---

### AI-Assisted Editing

A user may write a draft themselves and then use an AI tool to clean up grammar, improve word choice, or smooth out phrasing. The resulting content is a hybrid: the ideas and structure are human-authored, but the surface-level writing patterns have been altered by AI. The stylometric heuristics will likely pick up on the AI's editing fingerprints (more uniform sentence length, polished punctuation), while the LLM classifier may find the semantic content too coherent and well-structured to read as unassisted human writing. The system will likely return `Uncertain` for this type of content, which is the honest answer — but AI-assisted editing will be a consistent source of `Uncertain` attributions.

---

### Short Texts

Both signals degrade on short submissions. The stylometric heuristics are explicitly discounted below 150 words (weighted down to 10%) because sentence count and vocabulary size are too small to produce stable statistics. The LLM classifier is also less reliable on short texts since there is less signal to evaluate. Shorter submissions (both AI-generated and human-written) are likely to land in `Uncertain`, or be misclassified.

---

## Spec Reflection

### How did the spec help guide implementation?

The spec helped guide me throughout the implementation of the project. I would constantly have the AI reference my `planning.md` file for the relevant sections for each part of the implementation. For example, when implementing the `POST /submit` endpoint, I gave the AI the `Detection Signals` + `Submission Workflow` diagram, and had the AI generate the Flask app and the signal functions. The spec made it easy to work with the AI, as key design decisions and workflow decisions are already clearly laid out.

### When did the implementation diverge from the spec?

My implementation diverged from the spec during the implementation of combining the signal scores. Initially, I had decided on a fixed weighted average between the two signals (60% LLM / 40% stylometric). However, I quickly realized from testing that this single fixed split would not be adequate for short texts. I ended up using a length-adaptive weighting approach (not outlined in the spec), which helped to mitigate a lot of the misclassification I was having with shorter text. 

---

## AI Usage

**Instance 1**
- **What I gave the AI:** I gave the AI my `Detection Signals` and `Uncertainty Representation` section of `planning.md` along with the `Submission Workflow` diagram in `planning.md` and asked it to generate the stylometric heuristic signal function, the scoring logic that combines both signal scores into a final confidence score, and to wire it into the endpoint.
- **What it produced:** The AI produced a working stylometric signal function that outputted a float (0 to 1). It also correctly implemented the combined scoring logic exactly as outlined in `planning.md` which gave a final confidence score. This was wired into the endpoint.
- **What I changed or overrode:** The AI's stylometric heuristic function was using punctuation variance instead of punctuation density as outlined in the spec. Although the function was "working", it was not using the correct metric to produce a score. I changed the AI's function to use punctuation density, as outlined in the spec. In addition, I corrected the logging behavior as the AI did not correctly update it to include the stylometric score in the database. 

**Instance 2**
- **What I gave the AI:** I gave the AI the `Transparency Label Design` and `Appeals Workflow` sections of `planning.md` and the `Appeal Workflow` diagram in the `planning.md` and asked it to generate the label text based on the attribution and the `POST /appeal` endpoint. 
- **What it produced:** The AI produced a working attribution to transparency label function that used my pre-decided text in `planning.md`. It also created a working `POST /appeal` endpoint that allowed users to provide their `content_id` and `appeal_reason` and have the system put the submission under review.
- **What I changed or overrode:** I changed the appeals workflow since the model was not correctly logging the appeal **AND** updating the submission. I believe that this was largely due to the schema of the database initially, so I changed the database to have two separate tables: `content` and `audit_log`. I made it so that `audit_log` holds all `POST /submit` and `POST /appeal` events in the table, with all the relevant data. The actual data for a submission is stored in the `content` table, and the entry is modified if an appeal is made.