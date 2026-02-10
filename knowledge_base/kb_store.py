import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Iterable, Tuple

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class KBChunk:
    """Knowledge base chunk."""
    id: str
    path: str
    title: str
    text: str
    tokens: List[str]
    category: str = "unknown"
    heading: str = ""


def _tokenize(text: str) -> List[str]:
    """Tokenize text with mixed English and CJK."""
    words = _WORD_RE.findall(text.lower())
    cjk = _CJK_RE.findall(text)
    return words + cjk


def _chunk_markdown(text: str, max_chars: int = 1200, overlap: int = 200) -> Iterable[Tuple[str, str]]:
    """Split markdown into chunks by headings and paragraphs, then by size."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    blocks: List[str] = []
    buf: List[str] = []
    current_heading = ""
    for ln in lines:
        if ln.startswith("#"):
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
            current_heading = ln.lstrip("#").strip()
            buf.append(ln)
        elif ln.strip() == "" and buf:
            blocks.append("\n".join(buf).strip())
            buf = []
        else:
            buf.append(ln)
    if buf:
        blocks.append("\n".join(buf).strip())

    for block in blocks:
        if len(block) <= max_chars:
            yield (current_heading, block)
            continue
        start = 0
        while start < len(block):
            end = min(len(block), start + max_chars)
            chunk = block[start:end]
            yield (current_heading, chunk)
            if end == len(block):
                break
            start = max(0, end - overlap)


def _infer_category(path: str, data_dir: str) -> str:
    rel = os.path.relpath(path, data_dir)
    parts = rel.split(os.sep)
    if parts:
        return parts[0]
    return "unknown"


def _derive_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return fallback


def _chunk_java(text: str, max_chars: int = 1200) -> Iterable[Tuple[str, str]]:
    """Split Java source code into chunks by classes and methods."""
    lines = [ln.rstrip() for ln in text.splitlines()]

    # Remove license header and package/import statements
    content_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("public class") or line.strip().startswith("class") or \
                line.strip().startswith("public interface") or line.strip().startswith("interface"):
            content_start = i
            break

    if content_start > 0:
        lines = lines[content_start:]

    current_class: List[str] = []
    current_method: List[str] = []
    brace_count = 0
    in_class = False
    in_method = False
    class_name = ""
    method_name = ""

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect class/interface start
        if not in_class and (stripped.startswith("public class") or stripped.startswith("class") or
                             stripped.startswith("public interface") or stripped.startswith("interface")):
            if current_class:
                # Save previous class
                class_text = "\n".join(current_class)
                if len(class_text) <= max_chars:
                    yield (f"Class: {class_name}", class_text)
                else:
                    # Split large classes
                    start = 0
                    part_num = 1
                    while start < len(class_text):
                        end = min(len(class_text), start + max_chars)
                        chunk = class_text[start:end]
                        yield (f"Class: {class_name} Part {part_num}", chunk)
                        if end == len(class_text):
                            break
                        start = end
                        part_num += 1
                current_class = []

            in_class = True
            class_name = stripped.split()[1] if len(stripped.split()) > 1 else "Unknown"
            current_class.append(line)
            brace_count = 0

        elif in_class:
            current_class.append(line)

            # Count braces to track scope
            brace_count += line.count('{')
            brace_count -= line.count('}')

            # Detect method start within class
            if brace_count == 1 and (stripped.startswith("public") or stripped.startswith("private") or
                                     stripped.startswith("protected") or stripped.startswith("void") or
                                     stripped.startswith("int") or stripped.startswith("String") or
                                     stripped.startswith("boolean") or stripped.startswith("long")):
                if "(" in stripped and ")" in stripped and not stripped.endswith(';'):
                    if current_method:
                        # Save previous method
                        method_text = "\n".join(current_method)
                        if len(method_text) <= max_chars:
                            yield (f"Method: {class_name}.{method_name}", method_text)
                        else:
                            start = 0
                            part_num = 1
                            while start < len(method_text):
                                end = min(len(method_text), start + max_chars)
                                chunk = method_text[start:end]
                                yield (f"Method: {class_name}.{method_name} Part {part_num}", chunk)
                                if end == len(method_text):
                                    break
                                start = end
                                part_num += 1
                        current_method = []

                    in_method = True
                    # Extract method name
                    method_parts = stripped.split()
                    for j, part in enumerate(method_parts):
                        if '(' in part:
                            method_name = part.split('(')[0]
                            break
                    current_method.append(line)

            elif in_method:
                current_method.append(line)

                # Check if method ends
                if brace_count <= 0:
                    in_method = False
                    if current_method:
                        method_text = "\n".join(current_method)
                        if len(method_text) <= max_chars:
                            yield (f"Method: {class_name}.{method_name}", method_text)
                        else:
                            start = 0
                            part_num = 1
                            while start < len(method_text):
                                end = min(len(method_text), start + max_chars)
                                chunk = method_text[start:end]
                                yield (f"Method: {class_name}.{method_name} Part {part_num}", chunk)
                                if end == len(method_text):
                                    break
                                start = end
                                part_num += 1
                        current_method = []

            # Check if class ends
            if brace_count <= 0:
                in_class = False

    # Handle last class
    if current_class:
        class_text = "\n".join(current_class)
        if len(class_text) <= max_chars:
            yield (f"Class: {class_name}", class_text)
        else:
            start = 0
            part_num = 1
            while start < len(class_text):
                end = min(len(class_text), start + max_chars)
                chunk = class_text[start:end]
                yield (f"Class: {class_name} Part {part_num}", chunk)
                if end == len(class_text):
                    break
                start = end
                part_num += 1

    # Handle last method
    if current_method:
        method_text = "\n".join(current_method)
        if len(method_text) <= max_chars:
            yield (f"Method: {class_name}.{method_name}", method_text)
        else:
            start = 0
            part_num = 1
            while start < len(method_text):
                end = min(len(method_text), start + max_chars)
                chunk = method_text[start:end]
                yield (f"Method: {class_name}.{method_name} Part {part_num}", chunk)
                if end == len(method_text):
                    break
                start = end
                part_num += 1


def build_index(data_dir: str, index_path: str) -> Dict[str, object]:
    """Build knowledge base index from markdown and Java files."""
    docs: List[KBChunk] = []
    total_tokens = 0
    for root, _, files in os.walk(data_dir):
        for fname in files:
            # Skip non-supported files
            if not (fname.endswith(".md") or fname.endswith(".java")):
                continue

            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue

            fallback_title = os.path.splitext(fname)[0]
            category = _infer_category(path, data_dir)
            idx = 0

            # Choose chunking strategy based on file type
            if fname.endswith(".java"):
                # Handle Java files
                title = f"Java Source: {fallback_title}"
                for heading, chunk_text in _chunk_java(text):
                    chunk_id = f"{os.path.relpath(path, data_dir)}#{idx}"
                    tokens = _tokenize(chunk_text)
                    total_tokens += len(tokens)
                    docs.append(KBChunk(
                        id=chunk_id,
                        path=path,
                        title=title,
                        text=chunk_text,
                        tokens=tokens,
                        category="java",
                        heading=heading,
                    ))
                    idx += 1
            else:
                # Handle markdown files
                title = _derive_title(text, fallback_title)
                for heading, chunk_text in _chunk_markdown(text):
                    chunk_id = f"{os.path.relpath(path, data_dir)}#{idx}"
                    tokens = _tokenize(chunk_text)
                    total_tokens += len(tokens)
                    docs.append(KBChunk(
                        id=chunk_id,
                        path=path,
                        title=title,
                        text=chunk_text,
                        tokens=tokens,
                        category=category,
                        heading=heading,
                    ))
                    idx += 1

    doc_count = len(docs) or 1
    avgdl = total_tokens / doc_count
    df: Dict[str, int] = {}
    for doc in docs:
        for t in set(doc.tokens):
            df[t] = df.get(t, 0) + 1

    index = {
        "data_dir": data_dir,
        "avgdl": avgdl,
        "doc_count": doc_count,
        "df": df,
        "docs": [
            {
                "id": d.id,
                "path": d.path,
                "title": d.title,
                "text": d.text,
                "tokens": d.tokens,
                "category": getattr(d, "category", "unknown"),
                "heading": getattr(d, "heading", ""),
            }
            for d in docs
        ],
    }
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    return index


def load_index(index_path: str) -> Dict[str, object]:
    """Load knowledge base index from file."""
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _bm25_score(query_tokens: List[str], doc_tokens: List[str], df: Dict[str, int], doc_count: int,
                avgdl: float) -> float:
    """Compute BM25-like score."""
    if not doc_tokens:
        return 0.0
    k1 = 1.5
    b = 0.75
    doc_len = len(doc_tokens)
    tf: Dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for t in query_tokens:
        if t not in tf:
            continue
        n = df.get(t, 0)
        idf = 0.0
        if n:
            idf = max(0.0, (doc_count - n + 0.5) / (n + 0.5))
        denom = tf[t] + k1 * (1 - b + b * (doc_len / avgdl))
        score += idf * (tf[t] * (k1 + 1)) / denom
    return score


def search(index: Dict[str, object], query: str, top_k: int = 3) -> List[Dict[str, str]]:
    """Search index and return top_k chunks."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    df = index.get("df", {})
    doc_count = int(index.get("doc_count", 1))
    avgdl = float(index.get("avgdl", 1.0))
    scored = []
    for doc in index.get("docs", []):
        score = _bm25_score(query_tokens, doc.get("tokens", []), df, doc_count, avgdl)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, doc in scored[:top_k]:
        results.append({
            "id": doc.get("id", ""),
            "path": doc.get("path", ""),
            "title": doc.get("title", ""),
            "category": doc.get("category", "unknown"),
            "heading": doc.get("heading", ""),
            "text": doc.get("text", ""),
            "score": score,
        })
    return results
