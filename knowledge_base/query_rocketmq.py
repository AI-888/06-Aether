#!/usr/bin/env python3
"""
Interactive query tool for RocketMQ knowledge base.
"""

import os
import sys
from pathlib import Path

# Add parent directory to path to import kb_store
sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_base.kb_store import load_index, search


def query_rocketmq_kb(query: str, top_k: int = 5, show_content: bool = True):
    """Query RocketMQ knowledge base with enhanced formatting."""
    index_path = os.path.join(os.path.dirname(__file__), "rocketmq_kb_index.json")
    
    if not os.path.exists(index_path):
        print("RocketMQ knowledge base index not found.")
        print("Please run build_rocketmq_kb.py first to build the index.")
        return
    
    # Load the index
    index = load_index(index_path)
    
    # Search for relevant content
    results = search(index, query, top_k=top_k)
    
    print(f"\n🔍 Search results for: '{query}'")
    print("=" * 80)
    
    if not results:
        print("❌ No matching results found.")
        print("\n💡 Suggestions:")
        print("- Try different keywords (e.g., 'consumer', 'broker', 'message queue')")
        print("- Search for specific classes (e.g., 'DefaultMQPushConsumer', 'MessageQueue')")
        print("- Look for error types (e.g., 'MQClientException', 'RemotingException')")
        return
    
    for i, result in enumerate(results, 1):
        print(f"\n{i}. 📄 {result.get('title', 'Unknown')}")
        print(f"   📁 Category: {result.get('category', 'unknown')}")
        print(f"   📍 Heading: {result.get('heading', 'N/A')}")
        print(f"   📊 Score: {result.get('score', 0):.4f}")
        print(f"   📍 File: {os.path.basename(result.get('path', 'N/A'))}")
        
        if show_content:
            text = result.get('text', '')
            # Clean up and format the content
            lines = text.split('\n')
            preview_lines = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('//') and not line.startswith('/*'):
                    preview_lines.append(line)
                    if len(preview_lines) >= 5:  # Show first 5 meaningful lines
                        break
            
            if preview_lines:
                print(f"   📝 Content preview:")
                for j, line in enumerate(preview_lines, 1):
                    print(f"      {j}. {line}")
                    
            # Show method/class signature if available
            if "Method:" in result.get('heading', '') or "Class:" in result.get('heading', ''):
                # Extract signature from first few lines
                signature_lines = []
                for line in lines:
                    if line.strip() and not line.strip().startswith('//'):
                        signature_lines.append(line.strip())
                        if '{' in line or ';' in line:
                            break
                if signature_lines:
                    signature = ' '.join(signature_lines)
                    print(f"   🏷️  Signature: {signature[:200]}..." if len(signature) > 200 else f"   🏷️  Signature: {signature}")
    
    print("\n" + "=" * 80)
    print(f"📈 Found {len(results)} results for your query.")


def show_rocketmq_categories():
    """Show available categories in the RocketMQ knowledge base."""
    index_path = os.path.join(os.path.dirname(__file__), "rocketmq_kb_index.json")
    
    if not os.path.exists(index_path):
        print("Knowledge base not found. Please build it first.")
        return
    
    index = load_index(index_path)
    categories = {}
    
    for doc in index.get('docs', []):
        category = doc.get('category', 'unknown')
        categories[category] = categories.get(category, 0) + 1
    
    print("\n📂 RocketMQ Knowledge Base Categories:")
    print("=" * 50)
    for category, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        print(f"   {category}: {count:,} documents")
    print(f"\n📊 Total documents: {index.get('doc_count', 0):,}")


def show_search_tips():
    """Show helpful search tips for RocketMQ knowledge base."""
    print("\n💡 RocketMQ Knowledge Base Search Tips:")
    print("=" * 60)
    print("1. Search by class names:")
    print("   - 'DefaultMQPushConsumer' - Consumer implementation")
    print("   - 'MessageQueue' - Message queue structure")
    print("   - 'MQAdminImpl' - Admin operations")
    print("\n2. Search by concepts:")
    print("   - 'message listener' - Message consumption")
    print("   - 'offset store' - Message offset management")
    print("   - 'pull message' - Message pulling mechanism")
    print("\n3. Search by error types:")
    print("   - 'MQClientException' - Client-side errors")
    print("   - 'RemotingException' - Network errors")
    print("   - 'MQBrokerException' - Broker-side errors")
    print("\n4. Search by components:")
    print("   - 'broker' - Message broker")
    print("   - 'namesrv' - Name server")
    print("   - 'consumer group' - Consumer management")
    print("\n5. Search by features:")
    print("   - 'transaction message' - Transaction support")
    print("   - 'delay message' - Delayed messages")
    print("   - 'order message' - Ordered messages")


def interactive_mode():
    """Interactive query interface."""
    print("🚀 RocketMQ Knowledge Base Query Tool")
    print("=" * 50)
    print("This tool allows you to search through the complete RocketMQ Java source code.")
    print("\nAvailable commands:")
    print("  - Enter your search query to find relevant code")
    print("  - 'categories' - Show available categories")
    print("  - 'tips' - Show search tips")
    print("  - 'quit' or 'exit' - Exit the tool")
    
    while True:
        try:
            user_input = input("\n🔍 Query: ").strip()
            
            if user_input.lower() in ['quit', 'exit', 'q']:
                print("👋 Goodbye!")
                break
            elif user_input.lower() == 'categories':
                show_rocketmq_categories()
            elif user_input.lower() == 'tips':
                show_search_tips()
            elif not user_input:
                continue
            else:
                query_rocketmq_kb(user_input, top_k=5, show_content=True)
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")


def main():
    """Main function."""
    if len(sys.argv) == 1:
        # Interactive mode
        interactive_mode()
    else:
        # Command line mode
        query = " ".join(sys.argv[1:])
        query_rocketmq_kb(query, top_k=5, show_content=True)


if __name__ == "__main__":
    main()