
# utils.py
import re
from math import ceil

def chunk_text(text, chunk_size=2000):
    """Chunk text into roughly chunk_size characters, trying to split at paragraph boundaries."""
    paragraphs = text.split('\n\n')
    chunks = []
    current = []
    current_len = 0
    for p in paragraphs:
        plen = len(p) + 2
        if current_len + plen > chunk_size and current:
            chunks.append('\n\n'.join(current))
            current = [p]
            current_len = plen
        else:
            current.append(p)
            current_len += plen
    if current:
        chunks.append('\n\n'.join(current))
    return chunks
