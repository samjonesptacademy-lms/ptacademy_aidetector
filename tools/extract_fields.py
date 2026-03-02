"""
Extract all PDF form field names and values from a PDF file.
Usage: python tools/extract_fields.py <path_to_pdf> [> output.json]
"""
from pypdf import PdfReader
import sys
import json


def safe_get_fields(reader):
    """Safely extract form fields, handling malformed appearance dictionaries."""
    try:
        return reader.get_fields() or {}
    except KeyError:
        # Fallback: manually extract fields from annotations
        fields = {}
        for page in reader.pages:
            if '/Annots' not in page:
                continue
            for annot in page['/Annots']:
                try:
                    obj = annot.get_object()
                    if obj.get('/Subtype') == '/Widget':
                        name = obj.get('/T')
                        if name:
                            fields[str(name)] = obj
                except Exception:
                    pass
        return fields


if len(sys.argv) < 2:
    print("Usage: python tools/extract_fields.py <path_to_pdf>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
reader = PdfReader(path)
fields = safe_get_fields(reader)
output = {}
for name, field in fields.items():
    try:
        val = field.get('/V', '')
        ft = field.get('/FT', '')
        output[name] = {"type": str(ft), "sample_value": str(val)[:80]}
    except Exception:
        output[name] = {"type": "unknown", "sample_value": ""}

print(json.dumps(output, indent=2))
