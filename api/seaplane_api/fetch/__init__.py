"""Upstream feeds. One module per source; every call returns `(payload, error)` and never raises.

A failure here degrades exactly one input: the caller appends `error` to `briefing.errors`, sets
that `sources` entry to `null`, and carries on. Only a total Open-Meteo failure empties `days` and
nulls `outlook`, and even then the file is still written.
"""

from .http import USER_AGENT, get_json, get_text

__all__ = ["USER_AGENT", "get_json", "get_text"]
