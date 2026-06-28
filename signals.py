import json
import math
import os
import re

from groq import Groq
from pydantic import BaseModel

_groq_client = None


def _get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


class _ClassifierOutput(BaseModel):
    score: float


def llm_classifier(text: str) -> float:
    """
    Signal 1: LLM Classifier.

    Uses an LLM to evaluate the semantic meaning, tone, and context of the
    submitted text. Returns a confidence score between 0.0 (likely human) and
    1.0 (likely AI-generated).
    """
    system_prompt = (
        "You are an expert AI-authorship detector. Estimate the probability that the "
        "submitted text was generated or heavily produced by an AI language model. "
        "Return a JSON object with a single key 'score' set to a decimal between 0.0 "
        "(certainly human) and 1.0 (certainly AI).\n\n"
        "Judge the underlying composition, NOT the surface tone. Two cautions:\n"
        "- A formal or academic register is NOT by itself evidence of AI. Many humans "
        "write formally; look for a genuine authorial perspective, specific stakes, or "
        "natural unevenness before scoring formal prose as AI.\n"
        "- A casual or first-person tone does NOT rule out AI. Lightly edited AI often "
        "adopts a conversational opener layered over hallmark AI structure.\n\n"
        "Scoring guide:\n"
        "- 0.00-0.25 (likely human): personal voice, idiosyncrasy, mild errors, uneven "
        "structure, specific lived detail, informal asides.\n"
        "- 0.25-0.50 (leans human): coherent and possibly formal, but with real "
        "authorial perspective, specific stakes, or natural unevenness.\n"
        "- 0.50-0.80 (leans AI): smooth, balanced, slightly generic; may show light "
        "human editing or a conversational opener over tidy AI scaffolding "
        "(neat 'on one hand / on the other' framing, hedged generalities, "
        "'studies show' without specifics).\n"
        "- 0.80-1.00 (likely AI): hallmark AI register: formulaic transitions "
        "('It is important to note', 'Furthermore'), evenly weighted balanced points, "
        "abstract and non-committal, no specific lived detail.\n\n"
        "Return only the JSON object."
    )

    client = _get_groq_client()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw = response.choices[0].message.content
    parsed = _ClassifierOutput.model_validate(json.loads(raw))
    return parsed.score


def stylometric_heuristics(text: str) -> float:
    """
    Signal 2: Stylometric Heuristics.

    Measures statistical writing patterns and combines them into a confidence
    score between 0.0 (likely human) and 1.0 (likely AI-generated).

    Three sub-signals, equally weighted:
      - Sentence length variance: low variance (uniform lengths) → AI-like
      - Vocabulary richness (type-token ratio): low TTR → AI-like
      - Punctuation pattern variance: low variance across sentences → AI-like
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return 0.5

    # Sub-signal 1: sentence length variance
    word_counts = [len(s.split()) for s in sentences]
    if len(word_counts) > 1:
        mean_len = sum(word_counts) / len(word_counts)
        std_dev = math.sqrt(sum((n - mean_len) ** 2 for n in word_counts) / len(word_counts))
    else:
        std_dev = 0.0
    # std_dev == 0 (perfectly uniform) → 1.0; std_dev >= 12 (highly varied) → 0.0
    sentence_score = max(0.0, min(1.0, 1.0 - std_dev / 12.0))

    # Sub-signal 2: vocabulary richness (type-token ratio)
    words = re.findall(r"\b\w+\b", text.lower())
    ttr = len(set(words)) / len(words) if words else 1.0
    # High TTR (rich vocab) → human-like (low score); low TTR → AI-like (high score)
    vocab_score = 1.0 - ttr

    # Sub-signal 3: punctuation density (formal punctuation per word)
    # AI prose uses commas, semicolons, and colons more heavily than human writing.
    # Density is more reliable than variance for the short texts typical in submissions.
    punct_re = re.compile(r"[,;:]")
    punct_density = len(punct_re.findall(text)) / max(len(words), 1)
    # Typical human prose: 0.02–0.06 per word; AI prose: 0.08–0.15+
    # Scale so that density >= 0.10 maps to 1.0
    punct_score = min(1.0, punct_density / 0.10)

    return round((sentence_score + vocab_score + punct_score) / 3.0, 4)


_TRANSPARENCY_TEXT = {
    "High-Confidence Human": "This content was most likely written by a human.",
    "Uncertain": (
        "We weren't able to determine whether this content was written by a human "
        "or generated by AI. This can happen when the content is too short or when "
        "our analysis returns mixed results."
    ),
    "High-Confidence AI": "This content shows strong signs of being AI-generated.",
}


def generate_label(confidence_score: float) -> tuple[str, str]:
    """
    Maps a combined confidence score to (internal_label, transparency_text).

    Thresholds (from planning.md):
      0.00–0.25 → High-Confidence Human
      0.25–0.80 → Uncertain
      0.80–1.00 → High-Confidence AI
    """
    if confidence_score < 0.25:
        label = "High-Confidence Human"
    elif confidence_score < 0.80:
        label = "Uncertain"
    else:
        label = "High-Confidence AI"
    return label, _TRANSPARENCY_TEXT[label]


def combine_signals(llm_score: float, stylometric_score: float, text: str) -> float:
    """
    Combines Signal 1 (LLM Classifier) and Signal 2 (Stylometric Heuristics)
    into a single confidence score via weighted average.

    The weighting is length-adaptive. Stylometric heuristics are unreliable on
    short text (sentence-length variance is dominated by sentence count and TTR
    is artificially high), so the stylometric signal is heavily discounted below
    150 words and given more influence once the text is long enough to stabilize:
      - < 150 words: 90% LLM / 10% stylometric
      - >= 150 words: 60% LLM / 40% stylometric

    Returns a score between 0.0 (likely human) and 1.0 (likely AI-generated).
    """
    word_count = len(re.findall(r"\b\w+\b", text))
    if word_count < 150:
        llm_weight = 0.90
    else:
        llm_weight = 0.60
    return round(llm_weight * llm_score + (1.0 - llm_weight) * stylometric_score, 4)
