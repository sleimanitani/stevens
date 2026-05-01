"""PDF tooling.

Module-load registers the built-in strategies (native_text, ocr_fallback,
docling) so the dispatcher can route requests at runtime.
"""

# Side-effect imports — each strategy module calls register() at load.
from .strategies import docling, native_text, ocr_fallback  # noqa: F401
