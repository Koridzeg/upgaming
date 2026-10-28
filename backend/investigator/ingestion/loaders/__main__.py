"""Print what a file turns into: python -m investigator.ingestion.loaders FILE [FILE ...]"""

import json
import sys
from pathlib import Path

from investigator.ingestion.loaders import load_document

for name in sys.argv[1:]:
    path = Path(name)
    document = load_document(path.name, path.read_bytes())
    print(f"=== {path.name}\ntitle:    {document.title}\nformat:   {document.file_format}")
    print(f"metadata: {json.dumps(document.metadata, ensure_ascii=False, default=str)}")
    for i, block in enumerate(document.blocks):
        where = f"p{block.page}" if block.page else ""
        level = f"h{block.level}" if block.kind == "heading" else ""
        text = block.text.replace("\n", " / ")
        print(f"{i:>3} {block.kind:<10}{level:<3}{where:<4} {text[:110]}")
    print()
