"""LLM chat service (Task 13): `answer_query`.

Takes a user's question plus Task 12's `RetrievedContext` (top-k knowledge
base articles + whatever driver/vehicle/trip/driving-event rows were
resolved), builds a context-grounded prompt, sends it to Ollama via
`app.ai.llm.chat_completion` (the single LLM-call module, per the plan's
Global Constraints -- this module never touches `httpx` directly), and
returns a `ChatAnswer` (`text` + a heuristic `confidence`).

Compliance requirement (ASS2's fallback mechanism, non-negotiable): the
prompt instructs the model to answer ONLY from the supplied context, and to
reply with the EXACT string `FALLBACK_TEXT` -- "I am unable to find that
information" -- verbatim, not paraphrased, when the context doesn't support
an answer. Task 14's escalation logic (and potentially a user-facing display)
may match against this exact string, so it must never be reworded here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.llm import chat_completion
from app.ai.retrieval import DEFAULT_TOP_K, RetrievedContext

#: Verbatim fallback text the LLM is instructed to reply with when the
#: supplied context doesn't support an answer. Must match exactly -- do not
#: paraphrase or add punctuation.
FALLBACK_TEXT = "I am unable to find that information"

_SYSTEM_PROMPT = (
    "You are a support assistant for a fleet telematics platform. Answer the "
    "user's question using ONLY the information given in the 'Context' "
    "section below the question -- never use outside knowledge, and never "
    "guess or make up details that are not present in the context. "
    f'If the context does not contain enough information to answer the '
    f'question, you MUST reply with exactly this sentence and nothing else: '
    f'"{FALLBACK_TEXT}"'
)


@dataclass
class ChatAnswer:
    """The result of one `answer_query` call: the LLM's reply text plus a
    heuristic confidence score (see `_compute_confidence` for the formula
    and its reasoning)."""

    text: str
    confidence: float


def _format_articles(articles: list) -> str:
    if not articles:
        return "(no knowledge base articles were retrieved for this question)"
    parts = []
    for article in articles:
        category = f" [{article.category}]" if article.category else ""
        parts.append(f"- Article: {article.title}{category}\n  {article.content}")
    return "\n".join(parts)


def _format_driving_events(driving_events: list) -> str:
    if not driving_events:
        return "(no driving events)"
    lines = []
    for event in driving_events:
        event_type = getattr(event.event_type, "value", event.event_type)
        details = f" -- {event.details}" if event.details else ""
        location = f" at {event.location}" if event.location else ""
        lines.append(f"  - {event_type} at {event.event_time}{location}{details}")
    return "\n".join(lines)


def _format_telematics_context(context: RetrievedContext) -> str:
    if not any([context.driver, context.vehicle, context.trip, context.driving_events]):
        return "(no driver/vehicle/trip data was resolved for this question)"

    parts = []
    if context.driver is not None:
        parts.append(f"Driver: {context.driver.full_name} (license {context.driver.license_number})")
    if context.vehicle is not None:
        parts.append(
            f"Vehicle: {context.vehicle.make} {context.vehicle.model} "
            f"({context.vehicle.registration_number})"
        )
    if context.trip is not None:
        parts.append(
            f"Trip: started {context.trip.start_time}, ended {context.trip.end_time}, "
            f"distance {context.trip.distance_km} km"
        )
    if context.driving_events:
        parts.append("Driving events:\n" + _format_driving_events(context.driving_events))
    return "\n".join(parts)


def build_prompt_messages(query: str, context: RetrievedContext) -> list[dict[str, str]]:
    """Build the `messages` array passed to `chat_completion`: a system
    message with the answer-only-from-context + verbatim-fallback
    instruction, and a user message that embeds the real retrieved content
    (KB article titles/content, driver/vehicle/trip/driving-event data) --
    not a generic placeholder."""
    context_block = (
        "Knowledge base articles:\n"
        f"{_format_articles(context.articles)}\n\n"
        "Telematics data:\n"
        f"{_format_telematics_context(context)}"
    )
    user_content = f"Question: {query}\n\nContext:\n{context_block}"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


#: Minimum cosine SIMILARITY (see `RetrievedContext.article_similarities`)
#: the BEST retrieved article must clear before "articles were retrieved"
#: earns any confidence credit at all -- final-review Fix 4. POC-level
#: heuristic, not a calibrated number: `build_top_k_articles_query` has no
#: distance/relevance cutoff and always returns `top_k` rows whenever the KB
#: has that many, however semantically distant they are from the query, so
#: "N articles came back" on its own said nothing about relevance (against
#: the seeded ~10-article KB, this previously kept confidence pinned near
#: 0.80-0.95 almost regardless of query, since count alone was rewarded).
#: Below this floor, retrieval is treated the same as having found nothing.
_ARTICLE_RELEVANCE_FLOOR = 0.3


def _compute_confidence(context: RetrievedContext, answer_text: str) -> float:
    """POC-level heuristic confidence score -- NOT a claim of true model
    calibration (Ollama's `/api/chat` doesn't expose logprobs/log-likelihoods
    we could use for that). Proxies "how much did retrieval actually find,
    and how relevant was it" on two axes:

    1. Knowledge-base articles: gated on BOTH how many were retrieved (out
       of `DEFAULT_TOP_K`, as before) AND how relevant the single BEST
       (highest cosine-similarity) retrieved article actually was
       (`context.article_similarities` -- final-review Fix 4). Below
       `_ARTICLE_RELEVANCE_FLOOR`, the article-count bonus is withheld
       entirely, even if `top_k` articles came back -- a pile of irrelevant
       articles is not evidence the answer is grounded. From the floor up to
       a similarity of 1.0, the bonus scales linearly, so "found 3 nearly-
       identical-to-the-query articles" scores meaningfully higher than
       "found 3 borderline-relevant articles", not just "found articles" vs
       "found none".
    2. Whether any driver/vehicle/trip/driving-event data was resolved at
       all (a boolean bump, since telematics context is either present or
       not -- there's no partial-match notion for it the way there is for
       article count/relevance).

    If NEITHER axis found anything (no articles AND no telematics rows),
    there was nothing to ground an answer in, so confidence is pinned to a
    fixed low floor (0.1) regardless of what the LLM said.

    Separately, if the LLM's own answer is (or contains) the exact
    `FALLBACK_TEXT`, the model itself is reporting it couldn't answer from
    the context -- that's the strongest available signal that the answer is
    not usable, so confidence is capped at the same low floor even when
    retrieval did find articles/telematics data (e.g. found context that
    turned out not to actually address the question).
    """
    has_articles = bool(context.articles)
    has_telematics = bool(
        context.driver or context.vehicle or context.trip or context.driving_events
    )

    if not has_articles and not has_telematics:
        base_confidence = 0.1
    else:
        base_confidence = 0.35
        if has_articles:
            best_similarity = max(context.article_similarities, default=0.0)
            if best_similarity > _ARTICLE_RELEVANCE_FLOOR:
                relevance_scale = min(
                    (best_similarity - _ARTICLE_RELEVANCE_FLOOR)
                    / (1.0 - _ARTICLE_RELEVANCE_FLOOR),
                    1.0,
                )
                base_confidence += (
                    0.15 * min(len(context.articles), DEFAULT_TOP_K) * relevance_scale
                )
        if has_telematics:
            base_confidence += 0.15
        base_confidence = min(base_confidence, 0.95)

    if FALLBACK_TEXT in answer_text:
        return min(base_confidence, 0.1)

    return base_confidence


def answer_query(query: str, retrieved_context: RetrievedContext) -> ChatAnswer:
    """Answer `query` using `retrieved_context` as the only allowed source of
    facts: build a context-grounded prompt (`build_prompt_messages`), call
    the LLM via `chat_completion`, and return a `ChatAnswer` with a heuristic
    `confidence` (see `_compute_confidence`)."""
    messages = build_prompt_messages(query, retrieved_context)
    answer_text = chat_completion(messages)
    confidence = _compute_confidence(retrieved_context, answer_text)
    return ChatAnswer(text=answer_text, confidence=confidence)
