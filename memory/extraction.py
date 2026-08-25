"""Structured field extraction for the knowledge base: given a document's
extracted text, ask a local LLM to pull out whatever fields are actually
present (name, ID numbers, dates, amounts, ...) as flat key-value pairs.

Deliberately always local (direct `ollama.chat`, bypassing
agent/llm_client.py's multi-provider abstraction) regardless of which
provider is active for chat — this exists specifically to make sense of
ID-card-style documents (Aadhaar, PAN, ...), and that kind of data should
never leave the machine just because the user happens to have switched
their chat model to a cloud provider.

Fail-soft, same philosophy as agent/tts.py and memory/ocr.py: any failure
(Ollama unreachable, malformed JSON, wrong shape) returns {} rather than
raising — a document that can't be structured just behaves exactly like it
did before this existed (plain chunked semantic search).
"""
import json
import logging
import re

from config import config

logger = logging.getLogger("assistant.extraction")

# Deterministic sanity checks for a handful of well-known Indian ID-document
# fields with a strict, checkable shape — a backstop for a failure mode
# confirmed live: the prompt below already asks the model to omit garbled/
# illegible values, but that instruction isn't reliably followed (the same
# instruction-following gap already seen with tool-calling and with table
# reproduction elsewhere in this codebase — see agent/loop.py's
# is_structured_table comment). A real Aadhaar card re-upload extracted
# "Date of Birth": "1947" — a bare year, not a real date — which passed
# straight through untouched and was shown to the user as fact. Only
# fields whose name matches one of these known keywords get validated;
# everything else is left as the prompt's own instructions produced it, so
# this doesn't try to build a full per-document-type schema — just closes
# off the handful of formats unambiguous enough to check without risking a
# false rejection of a legitimately-formatted value.
_DATE_RE = re.compile(r"^\d{1,2}[\s./-]+(?:\d{1,2}|[A-Za-z]{3,9})[\s./-]+\d{2,4}$")
_AADHAAR_RE = re.compile(r"^\d{4}\s?\d{4}\s?\d{4}$")
_PAN_RE = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
_PIN_RE = re.compile(r"^\d{6}$")
_MOBILE_RE = re.compile(r"^(?:\+?91[\s-]?)?\d{10}$")

_FIELD_VALIDATORS: list[tuple[str, re.Pattern]] = [
    ("date of birth", _DATE_RE),
    ("dob", _DATE_RE),
    ("aadhaar number", _AADHAAR_RE),
    ("aadhar number", _AADHAAR_RE),
    ("pan number", _PAN_RE),
    ("pin code", _PIN_RE),
    ("pincode", _PIN_RE),
    ("mobile", _MOBILE_RE),
    ("phone", _MOBILE_RE),
]


def _is_valid_field_value(name: str, value: str) -> bool:
    key = name.strip().lower()
    for keyword, pattern in _FIELD_VALIDATORS:
        if keyword in key:
            return bool(pattern.match(value.strip()))
    return True  # no validator for this field name — leave as the model produced it

# ID cards/forms are short and front-loaded — the fields that matter are
# always near the top. Capping input keeps this fast and keeps a long
# document (a thesis, a long article) from being sent through wholesale
# just to correctly conclude "nothing structured here."
_MAX_INPUT_CHARS = 6000

# Plain string + .replace() rather than str.format()/an f-string — the
# example JSON below is full of literal { } characters that would otherwise
# collide with format-placeholder syntax.
_PROMPT = """Extract clearly identifiable fields from the document text below as a flat JSON object of "Field Name": "value" pairs. The text may be OCR output and contain misreads.

Rules:
- A field's VALUE must be a coherent, legible name/number/date/address/etc. If the OCR text for a would-be field is garbled, cut off, or doesn't form a real value (random-looking characters, no discernible words), OMIT that field entirely. It is much better to return fewer, correct fields than to include a wrong or nonsensical one.
- Only include a field if its value is actually present as text — do not add a field just because a document of this type would typically have one (e.g. don't add "Issuing Authority: Government of India" unless that specific text is actually legible in the source).
- Use natural field names as they appear or are clearly implied (e.g. "Name", "Date of Birth", "Aadhaar Number", "PAN Number", "Address", "Invoice Number", "Total Amount") — prefer the short, standard name for well-known ID document fields over a longer paraphrase.
- A value must be a single plain string, on one line — never include a literal newline inside a value (join multi-line text like an address with ", " instead).

Also include a "_document_type" key with a short 2-4 word guess at what kind of document this is (e.g. "Aadhaar Card", "PAN Card", "Invoice", "Resume", "General Document").

If the text is just prose with no clear structured fields (an article, a story, general notes), or if OCR quality is too poor to confidently extract anything, respond with exactly: {"_document_type": "General Document"}

Respond with ONLY the JSON object — no explanation, no markdown code fences.

Document text:
---
__DOCUMENT_TEXT__
---"""


def extract_structured_fields(text: str) -> dict:
    """Best-effort structured extraction. Returns {} on any failure —
    callers should treat that as "nothing structured found," not an error."""
    try:
        import ollama

        response = ollama.chat(
            model=config.ollama_model,
            messages=[{"role": "user", "content": _PROMPT.replace("__DOCUMENT_TEXT__", text[:_MAX_INPUT_CHARS])}],
            format="json",
            options={"temperature": 0},
        )
        data = json.loads(response["message"]["content"])
        if not isinstance(data, dict):
            return {}

        validated = {}
        for key, value in data.items():
            if key == "_document_type" or _is_valid_field_value(key, str(value)):
                validated[key] = value
            else:
                logger.warning("Dropping %r=%r - failed field-format validation", key, value)
        return validated
    except Exception:
        logger.exception("Structured extraction failed — falling back to unstructured")
        return {}
