"""Shared helper for reading text out of an ADK Event."""

from google.adk.events import Event


def extract_event_text(event: Event | None) -> str:
    if not event:
        return ""
    if isinstance(event.message, str):
        return event.message
    if event.content and event.content.parts:
        part = event.content.parts[0]
        if hasattr(part, 'text') and part.text:
            return part.text
        if isinstance(part, dict) and 'text' in part:
            return part['text']
    return ""
