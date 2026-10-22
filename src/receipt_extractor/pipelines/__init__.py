"""Extraction pipelines behind one protocol (TRD §4.2): image bytes → ``ExtractionResult``.

- ``pipeline_a`` — RapidOCR (CPU) → Groq ``gpt-oss-20b`` → ``Receipt`` (M1.3).

Heavy dependencies (ONNX Runtime, OpenCV, LiteLLM) load on first use, never at import time.
"""
