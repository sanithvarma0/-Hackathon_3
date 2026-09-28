"""Langfuse tracing (BUILD_PLAN.md 7.5, 17.4).

One trace per agent run, grouped by incident ID as the Langfuse session. LLM calls are captured
automatically by `langfuse.openai`; nodes, tools and memory calls are explicit child spans. When
Langfuse keys are absent (tests, CI) every call is a no-op, so tracing can never break the agent.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from backend.config import Settings

SpanType = Literal["span", "agent", "tool", "chain", "retriever", "evaluator", "guardrail"]


class Span:
    """Handle for an open span; `update` is safe to call whether or not tracing is enabled."""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner

    def update(self, **fields: Any) -> None:
        if self._inner is not None:
            self._inner.update(**fields)


class Tracer:
    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def run(self, incident_id: str, **metadata: str) -> Iterator[Span]:
        """Root span for one agent run on one incident."""
        if self._client is None:
            yield Span()
            return
        from langfuse import propagate_attributes

        with (
            self._client.start_as_current_observation(
                name=f"incident {incident_id}", as_type="agent", metadata=metadata
            ) as root,
            propagate_attributes(
                session_id=incident_id,
                trace_name=f"incident {incident_id}",
                metadata={k: str(v) for k, v in metadata.items()},
            ),
        ):
            yield Span(root)

    @contextmanager
    def span(self, name: str, as_type: SpanType = "span", input: Any = None) -> Iterator[Span]:
        if self._client is None:
            yield Span()
            return
        with self._client.start_as_current_observation(
            name=name, as_type=as_type, input=input
        ) as inner:
            yield Span(inner)

    def current_trace_url(self) -> str | None:
        if self._client is None:
            return None
        url: str | None = self._client.get_trace_url()
        return url

    def flush(self) -> None:
        if self._client is not None:
            self._client.flush()


def build_tracer(settings: Settings) -> Tracer:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return Tracer()
    from langfuse import Langfuse

    return Tracer(
        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    )
