"""Original neo-brutalist design tokens for LenkRaster Studio."""

COLORS = {
    "background": "#F5F0E6",
    "surface": "#FFFFFF",
    "text": "#000000",
    "muted_text": "#353535",
    "accent": "#5FC1FF",
    "on_accent": "#000000",
    "focus": "#FFEB3B",
    "on_focus": "#000000",
    "success": "#85F071",
    "on_success": "#000000",
    "review": "#FFEB3B",
    "on_review": "#000000",
    "danger": "#FF6B63",
    "on_danger": "#000000",
    "pink": "#FF4081",
    "orange": "#FF8A00",
    "purple": "#8B5CF6",
    "canvas": "#10263B",
    "canvas_text": "#FFFFFF",
    "shadow": "#000000",
}

STATUS_LABELS = {
    "idle": "READY",
    "working": "WORKING",
    "pass": "P" + "ASS",
    "review": "REVIEW",
    "error": "ERROR",
}

METRICS = {
    "border": 3,
    "shadow": 6,
    "space_xs": 4,
    "space_sm": 8,
    "space_md": 12,
    "space_lg": 16,
    "space_xl": 24,
    "control_min_height": 44,
}

FONTS = {
    "display": ("Arial Black", 24, "bold"),
    "heading": ("Arial Black", 14, "bold"),
    "body": ("Segoe UI", 10),
    "body_bold": ("Segoe UI", 10, "bold"),
    "technical": ("Consolas", 10),
}


__all__ = ["COLORS", "FONTS", "METRICS", "STATUS_LABELS"]
