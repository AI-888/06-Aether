#!/usr/bin/env python3
"""
Build knowledge base index from RocketMQ Java source code.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path to import kb_store
sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_base.kb_store import build_index, search


def count_java_files(data_dir: str) -> tuple[int, int]:
    """Count Java files and estimate total lines of code."""
    java_files = []
    total_lines = 0

    for root, _, files in os.walk(data_dir):
        for fname in files:
            if fname.endswith(".java"):
                path = os.path.join(root, fname)
                java_files.append(path)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        total_lines += len(lines)
                except Exception:
                    continue

    return len(java_files), total_lines


def main():
    """Build RocketMQ knowledge base index."""
    # Use rocketmq_531 directory as data source
    data_dir = os.path.join(os.path.dirname(__file__), "rocketmq_531")
    index_path = os.path.join(os.path.dirname(__file__), "rocketmq_kb_index.json")

    print("RocketMQ Knowledge Base Builder")
    print("=" * 50)
    print(f"Source directory: {data_dir}")
    print(f"Index file: {index_path}")

    # Check if source directory exists
    if not os.path.exists(data_dir):
        print(f"Error: Source directory '{data_dir}' does not exist!")
        return

    # Count Java files and lines
    print("\nScanning Java source files...")
    java_file_count, total_lines = count_java_files(data_dir)
    print(f"Found {java_file_count} Java files with approximately {total_lines:,} lines of code")

    if java_file_count == 0:
        print("No Java files found. Exiting.")
        return

    print("\nBuilding knowledge base index...")
    print("This may take a while for large codebases...")

    # Build the index
    try:
        index = build_index(data_dir, index_path)

        print("\nIndex built successfully!")
        print(f"- Total documents: {index.get('doc_count', 0):,}")
        print(f"- Average document length: {index.get('avgdl', 0):.2f}")
        print(f"- Unique terms: {len(index.get('df', {})):,}")

        # Test search functionality with RocketMQ specific queries
        print("\nTesting search functionality...")

        # Test with RocketMQ Java source code related queries
        test_queries = [
            "DefaultMQPushConsumer",
            "MessageQueue",
            "broker",
            "topic",
            "consumer group",
            "message listener",
            "pull message",
            "offset store",
            "MQClientException",
            "RemotingException"
        ]

        for query in test_queries:
            results = search(index, query, top_k=2)
            print(f"\nQuery: '{query}'")
            if results:
                for i, result in enumerate(results, 1):
                    print(f"  {i}. {result.get('title', 'N/A')}")
                    print(f"     Category: {result.get('category', 'unknown')}")
                    print(f"     Heading: {result.get('heading', 'N/A')}")
                    print(f"     Score: {result.get('score', 0):.4f}")
                    # Show first few lines of content
                    text = result.get('text', '')
                    preview = text[:200] + "..." if len(text) > 200 else text
                    print(f"     Preview: {preview}")
            else:
                print("  No results found")

        print(f"\nKnowledge base index saved to: {index_path}")
        print("You can now use the search functionality to query RocketMQ source code.")

    except Exception as e:
        print(f"Error building index: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
