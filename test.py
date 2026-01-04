import os
import re
import numpy as np
from typing import List, Dict, Any
from markitdown import MarkItDown
from openai import OpenAI

# Initialize Client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
md_parser = MarkItDown()

class RecursiveHeaderChunker:
    """
    Splits markdown by headers (Level 1-3) to preserve logical structure.
    If a section is too large, it sub-splits by paragraphs.
    """
    def __init__(self, max_chunk_size=800, overlap=100):
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def split_text(self, text: str) -> List[Dict[str, Any]]:
        # Regex to match headers like "# Title" or "### Section"
        # We use a capture group to keep the delimiter for reconstruction
        # This splits the text into [content, header, content, header...]
        header_pattern = r'(^#{1,3}\s+.*)'
        parts = re.split(header_pattern, text, flags=re.MULTILINE)
        
        chunks = []
        current_header = "General"
        
        # 'parts' will look like: ['', '# Header 1', 'Content...', '## Header 2', 'Content...']
        # We iterate and group them.
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # If it's a header, update current context
            if re.match(r'^#{1,3}\s+', part):
                current_header = part
            else:
                # It's content. If too big, sub-split.
                if len(part) > self.max_chunk_size:
                    sub_chunks = self._sub_split_paragraph(part)
                    for sub in sub_chunks:
                        chunks.append({
                            "text": f"{current_header}\n\n{sub}", # Inject context
                            "metadata": {"header": current_header}
                        })
                else:
                    chunks.append({
                        "text": f"{current_header}\n\n{part}",
                        "metadata": {"header": current_header}
                    })
        return chunks

    def _sub_split_paragraph(self, text: str) -> List[str]:
        """Simple fallback: split by double newline (paragraphs)"""
        paragraphs = text.split("\n\n")
        # Combine paragraphs until max_chunk_size is reached (simple logic)
        current_chunk = []
        current_len = 0
        final_chunks = []
        
        for p in paragraphs:
            if current_len + len(p) > self.max_chunk_size:
                final_chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p)
        
        if current_chunk:
            final_chunks.append("\n\n".join(current_chunk))
        return final_chunks

def get_embedding(text: str, model="text-embedding-3-small"):
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model=model).data[0].embedding

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# --- MAIN PIPELINE ---

def run_pipeline(file_path: str, user_query: str):
    # 1. Convert File to Markdown
    print(f"Parsing {file_path}...")
    result = md_parser.convert(file_path)
    raw_markdown = result.text_content
    
    # 2. Chunking
    chunker = RecursiveHeaderChunker(max_chunk_size=1500)
    chunks = chunker.split_text(raw_markdown)
    print(f"Generated {len(chunks)} chunks.")

    # 3. Embed All Chunks (In production, store these in a Vector DB)
    print("Generating embeddings...")
    for chunk in chunks:
        chunk['embedding'] = get_embedding(chunk['text'])

    # 4. Retrieval
    query_vec = get_embedding(user_query)
    
    # Calculate scores
    results = []
    for chunk in chunks:
        score = cosine_similarity(query_vec, chunk['embedding'])
        results.append((score, chunk))
    
    # Top 3 results
    results.sort(key=lambda x: x[0], reverse=True)
    top_chunks = results[:3]
    
    # 5. Generation (RAG)
    context_text = "\n\n".join([c[1]['text'] for c in top_chunks])
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Use the provided context to answer."},
            {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {user_query}"}
        ]
    )
    
    return response.choices[0].message.content

# Example Usage:
if __name__ == "__main__":
    answer = run_pipeline("/Users/saidheeraj/Downloads/resume.pdf", "sai dheeraj projects and Experience details")
    print(answer)
