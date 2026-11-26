"""Curated set of ayanamsas offered during LLM-based rectification.

Kept as a standalone list (rather than importing from app.engines.vedic)
so the LLM-based rectification flow in app.routers.rectify has no
dependency on the deterministic Swiss Ephemeris engine, which is no
longer wired into that flow (app.engines.vedic and
app.engines.significators are kept on disk, unimported, in case the
deterministic approach is ever revisited).

Mirrors app.engines.vedic.CURATED_AYANAMSAS -- keep the two in sync if
either changes.
"""
CURATED_AYANAMSAS = ["LAHIRI", "TRUE_CHITRA_PAKSHA", "KRISHNAMURTI", "PUSHYA_PAKSHA", "RAMAN"]
