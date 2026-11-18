"""Key phrases, named entities, and sentiment from Azure AI Language."""
from __future__ import annotations

import os

from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError

from errors import PipelineError

CHUNK_LIMIT = 5000
MAX_CHUNKS = 5


def chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[tuple[int, str]]:
    """Split text at sentence or word boundaries, keeping each chunk's offset."""
    chunks, start = [], 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            sentence_end = text.rfind(". ", start, end)
            space = text.rfind(" ", start, end)
            if sentence_end > start:
                end = sentence_end + 1
            elif space > start:
                end = space
        chunks.append((start, text[start:end]))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    return chunks


def _check(results: list) -> None:
    for result in results:
        if result.is_error:
            raise PipelineError("Azure Language could not analyze the transcript.", 502)


def analyze_text(text: str) -> dict:
    endpoint, key = os.environ.get("AZURE_LANGUAGE_ENDPOINT"), os.environ.get("AZURE_LANGUAGE_KEY")
    if not endpoint or not key:
        raise PipelineError("Azure Language is not configured on the server.", 500)

    chunks = chunk_text(text)
    if len(chunks) > MAX_CHUNKS:
        raise PipelineError("The transcript is too long to analyze.", 413)
    documents = [chunk for _, chunk in chunks]
    client = TextAnalyticsClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    try:
        phrase_results = client.extract_key_phrases(documents)
        entity_results = client.recognize_entities(documents)
        sentiment_results = client.analyze_sentiment(documents)
    except AzureError as exc:
        raise PipelineError("Azure Language could not be reached.", 502) from exc
    for results in (phrase_results, entity_results, sentiment_results):
        _check(results)

    phrases = list({
        phrase.lower(): phrase for result in phrase_results for phrase in result.key_phrases
    }.values())
    entities = [
        {
            "text": entity.text,
            "category": entity.category,
            "subcategory": entity.subcategory,
            "confidence": round(entity.confidence_score, 3),
            "offset": chunks[index][0] + entity.offset,
        }
        for index, result in enumerate(entity_results)
        for entity in result.entities
    ]
    scores = {
        label: round(
            sum(getattr(r.confidence_scores, label) for r in sentiment_results) / len(sentiment_results), 3
        )
        for label in ("positive", "neutral", "negative")
    }
    label = max(scores, key=scores.get)
    return {
        "key_phrases": phrases,
        "entities": entities,
        "sentiment": {"label": label, "score": scores[label], "scores": scores},
    }
