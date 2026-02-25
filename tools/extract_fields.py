"""
Extract all PDF form field names and values from a PDF file.
Usage: python tools/extract_fields.py <path_to_pdf> [> output.json]
"""
from pypdf import PdfReader
import sys
import json

if len(sys.argv) < 2:
    print("Usage: python tools/extract_fields.py <path_to_pdf>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
reader = PdfReader(path)
fields = reader.get_fields() or {}
output = {}
for name, field in fields.items():
    val = field.get('/V', '')
    ft = field.get('/FT', '')
    output[name] = {"type": str(ft), "sample_value": str(val)[:80]}

print(json.dumps(output, indent=2))
