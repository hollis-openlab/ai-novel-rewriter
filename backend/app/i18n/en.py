from __future__ import annotations

EN_MESSAGES: dict[str, str] = {
    # Stage labels
    "stage.analyze": "Analyze",
    "stage.mark": "Mark",
    "stage.rewrite": "Rewrite",
    "stage.import": "Import",
    "stage.split": "Split",
    "stage.assemble": "Assemble",

    # Stage errors
    "error.upstream_required": "Please complete the \"{upstream_label}\" stage first",

    # Review feedback
    "review.feedback_prefix": "[Review Feedback]",
    "review.fix_boundary": "[Fix Boundary]",

    # Scene type defaults
    "scene.manual_mark": "Manual Mark",

    # Export labels
    "export.chapter_heading": "Chapter {idx}",
    "export.original": "Original",
    "export.rewritten": "Rewritten",
    "export.toc": "Table of Contents",

    # Error messages
    "error.validation_failed": "Request validation failed",
    "error.not_found": "Resource not found",
    "error.internal": "Internal server error",
    "error.http": "HTTP error",
}
