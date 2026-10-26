from pathlib import Path

# Path where the pipeline drops output Excel files
OUTPUT_DIR = Path(r"C:\Users\RMahesh8\Downloads\AI_Auditor\AI_Auditor\output")

APP_TITLE   = "TPI Due Diligence — AI Audit Dashboard"
COMPANY     = "Johnson & Johnson"
DEPARTMENT  = "Global Audit & Assurance"

# Maps the exact strings written by the pipeline → UI colour tokens
RISK_COLORS: dict[str, dict] = {
    "Potential Risk": {
        "border":   "#C00000",
        "badge_bg": "#C00000",
        "card_bg":  "#FFF5F5",
        "text":     "#C00000",
        "icon":     "🔴",
    },
    "Further Review Required": {
        "border":   "#E65100",
        "badge_bg": "#E65100",
        "card_bg":  "#FFF8F0",
        "text":     "#E65100",
        "icon":     "🟠",
    },
    "No Risk Identified": {
        "border":   "#2E7D32",
        "badge_bg": "#2E7D32",
        "card_bg":  "#F1F8F4",
        "text":     "#2E7D32",
        "icon":     "🟢",
    },
}

FILTER_OPTIONS = list(RISK_COLORS.keys())

# Excel columns in display order for the "View Details" panel.
# (display_label, exact_header_in_excel)
DETAIL_COLUMNS: list[tuple[str, str]] = [
    ("Scope Area",              "Scope Area"),
    ("Risk Score",              "Risk Score"),
    ("Risk Description",        "Risk Description"),
    ("Risk Rationale",          "Risk Rationale"),
    ("Risk Score Reasoning",    "Risk Score Reasoning"),
    ("Source Details",          "Source Details"),
    ("Evidence Classification", "Evidence Classification"),
    ("Evidence Explanation",    "Evidence Explanation"),
    ("Calculation Evidence",    "Calculation Evidence"),
    ("Question",                "Question"),
    ("Response",                "Response"),
    ("Follow-up Question",      "Follow-up Question"),
    ("Follow-up Response",      "Follow-up Response"),
    ("Processed At",            "Processed At"),
]

ROWS_PER_PAGE = 20
