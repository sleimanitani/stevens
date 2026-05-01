"""Sphinx — the PDF agent (display name; code id ``pdf``).

Async-path handler for ``pdf.parse.requested.*``. Inspects each PDF and
routes to one of the strategies in ``skills.tools.pdf.strategies``
(native_text, ocr_fallback, docling, …). Pattern lifted from Hermes's
terminal-backend factory.

Synchronous ReAct agents continue to use the ``read_pdf`` skill, which
also routes through the dispatcher under the hood. Both paths converge
on the same strategy registry.
"""
