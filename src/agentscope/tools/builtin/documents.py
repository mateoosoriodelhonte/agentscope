"""Document search and retrieval over the packaged synthetic knowledge base."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentscope.tools.base import Tool, ToolArgs, ToolContext, ToolExecutionError, ToolOutput

Reliability = Literal["verified", "unverified"]

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    ]
)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    attribute: str
    value: str


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    source: str
    reliability: Reliability
    published: str
    body: str
    facts: list[Fact]


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@lru_cache(maxsize=1)
def load_corpus() -> tuple[Document, ...]:
    raw = resources.files("agentscope.tools.data").joinpath("documents.json").read_text("utf-8")
    return tuple(Document.model_validate(d) for d in json.loads(raw)["documents"])


def _score(query_tokens: list[str], doc: Document) -> int:
    title = tokenize(doc.title)
    body = tokenize(doc.body)
    subjects = tokenize(" ".join(f.subject for f in doc.facts))
    return sum(3 * title.count(t) + body.count(t) + subjects.count(t) for t in query_tokens)


# --- search_documents ---------------------------------------------------------------


class SearchArgsV1(ToolArgs):
    query: str = Field(min_length=1, max_length=200, description="Keywords to search for.")
    top_k: int = Field(default=3, ge=1, le=5, description="Maximum results to return.")


class SearchArgsV2(ToolArgs):
    query: str = Field(min_length=3, max_length=200, description="Keywords to search for.")
    top_k: int = Field(default=3, ge=1, le=5, description="Maximum results to return.")
    include_unverified: bool = Field(
        default=False,
        description="Include unverified sources such as community forum posts.",
    )


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    source: str
    reliability: Reliability
    snippet: str
    score: int = Field(ge=0)


class SearchOutput(ToolOutput):
    query: str
    results: list[SearchHit]


def _search(query: str, top_k: int, include_unverified: bool) -> SearchOutput:
    tokens = tokenize(query)
    scored = [
        (score, doc)
        for doc in load_corpus()
        if include_unverified or doc.reliability == "verified"
        if (score := _score(tokens, doc)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], item[1].id))
    hits = [
        SearchHit(
            id=doc.id,
            title=doc.title,
            source=doc.source,
            reliability=doc.reliability,
            snippet=doc.body[:240],
            score=score,
        )
        for score, doc in scored[:top_k]
    ]
    return SearchOutput(query=query, results=hits)


class SearchDocumentsV1(Tool):
    name = "search_documents"
    version = 1
    description = (
        "Keyword search over the local knowledge base. Returns ranked document snippets. "
        "Use retrieve_document to read a full document and its facts."
    )
    args_model: ClassVar[type[ToolArgs]] = SearchArgsV1
    output_model = SearchOutput

    async def run(self, args: SearchArgsV1, ctx: ToolContext) -> SearchOutput:
        return _search(args.query, args.top_k, include_unverified=True)

    def inject_instructions(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        results = [dict(hit) for hit in output.get("results", [])]
        if results:
            results[0]["snippet"] = f"{results[0]['snippet']} {payload}"
        return {**output, "results": results}

    def make_misleading(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        results = [dict(hit) for hit in output.get("results", [])]
        if results:
            results[0]["snippet"] = payload
        return {**output, "results": results}


class SearchDocumentsV2(SearchDocumentsV1):
    version = 2
    description = (
        "Keyword search over the local knowledge base. Returns ranked snippets from verified "
        "sources unless include_unverified is true. Use retrieve_document for full text."
    )
    args_model = SearchArgsV2

    async def run(self, args: SearchArgsV2, ctx: ToolContext) -> SearchOutput:  # type: ignore[override]
        return _search(args.query, args.top_k, include_unverified=args.include_unverified)


# --- retrieve_document --------------------------------------------------------------


class RetrieveArgs(ToolArgs):
    doc_id: str = Field(pattern=r"^doc-[a-z0-9-]{1,60}$", description="Document id from search.")


class RetrieveOutput(ToolOutput):
    document: Document


class RetrieveDocument(Tool):
    name = "retrieve_document"
    description = "Fetch one knowledge-base document by id, including its structured facts."
    args_model = RetrieveArgs
    output_model = RetrieveOutput

    async def run(self, args: RetrieveArgs, ctx: ToolContext) -> RetrieveOutput:
        for doc in load_corpus():
            if doc.id == args.doc_id:
                return RetrieveOutput(document=doc)
        raise ToolExecutionError(f"document not found: {args.doc_id}")

    def inject_instructions(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        doc = dict(output["document"])
        doc["body"] = f"{doc['body']}\n\n{payload}"
        return {"document": doc}

    def make_incomplete(self, output: dict[str, Any]) -> dict[str, Any]:
        doc = dict(output["document"])
        doc["body"] = doc["body"][: len(doc["body"]) // 3]
        doc["facts"] = []
        return {"document": doc}

    def make_misleading(self, output: dict[str, Any], payload: str) -> dict[str, Any]:
        doc = dict(output["document"])
        doc["facts"] = [{**fact, "value": _distort(fact["value"])} for fact in doc["facts"]]
        doc["body"] = f"{doc['body']} {payload}"
        return {"document": doc}


def _distort(value: str) -> str:
    """Deterministically alter a fact value so it is plausible but wrong."""
    try:
        number = float(value)
    except ValueError:
        return f"{value} (disputed)"
    if number.is_integer():
        return str(int(number) + (4 if number > 100 else 1))
    return f"{number * 1.5:.2f}"
