"""LLM column-mapping for questionnaire tables.

Identifies which columns hold the question, response, and (possibly MULTIPLE) follow-up question /
follow-up response roles — so a table with several follow-up rounds, or non-standard header names, is
read correctly without hardcoding. The LLM only MAPS columns; it never computes or invents data.
"""

QUESTIONNAIRE_COLUMN_SYSTEM = (
    "You map the columns of an audit questionnaire table to their roles. Return only the requested JSON."
)

QUESTIONNAIRE_COLUMN_USER_TEMPLATE = (
    "A questionnaire table has these COLUMNS (exact header strings):\n{headers}\n\n"
    "SAMPLE ROWS (header=value, several rows):\n{sample_block}\n\n"
    "Identify which column fills each role, using BOTH the header name and the sample VALUES (a "
    "question column holds question text; a response column holds answers). Return ONLY this JSON "
    "(null when a role is absent, [] when there are no follow-ups):\n"
    '{{"id": "<header or null>", "question": "<header>", "response": "<header>", '
    '"followup_questions": ["<header>", ...], "followup_responses": ["<header>", ...]}}\n'
    "Role definitions:\n"
    "- question  = the PRIMARY audit / IDR / planning question text column (exactly one).\n"
    "- response  = the company's or management's answer to that primary question (exactly one). If "
    "several response-like columns exist (e.g. 'Company Response' and 'Management Response'), pick the "
    "one that directly answers the primary question; do NOT pick a follow-up response.\n"
    "- followup_questions = auditor FOLLOW-UP question column(s) — a later round of questions. There "
    "may be MULTIPLE; list ALL of them in column order.\n"
    "- followup_responses = the response(s) to those follow-up questions (MULTIPLE allowed, in order).\n"
    "- id        = a unique row id / question-number column, if one exists.\n"
    "Rules: a 'follow-up response' is NOT the primary response. A 'Question No' / 'Question ID' column "
    "is the id, NOT the question. Each column fills at most ONE role. Use the EXACT header strings from "
    "the COLUMNS list — never invent a header."
)
