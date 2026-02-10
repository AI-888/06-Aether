#!/usr/bin/env python3
"""
Package RocketMQ Knowledge Base into a distributable zip file.
"""

import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime


def create_package_structure(temp_dir: str) -> dict:
    """Create organized directory structure for packaging."""

    # Define package structure
    structure = {
        'root': temp_dir,
        'docs': os.path.join(temp_dir, 'docs'),
        'tools': os.path.join(temp_dir, 'tools'),
        'knowledge_base': os.path.join(temp_dir, 'knowledge_base'),
        'source_code': os.path.join(temp_dir, 'source_code'),
        'examples': os.path.join(temp_dir, 'examples'),
    }

    # Create directories
    for dir_path in structure.values():
        os.makedirs(dir_path, exist_ok=True)

    return structure


def copy_knowledge_base_files(structure: dict, source_dir: str):
    """Copy knowledge base related files."""
    kb_files = [
        'kb_store.py',
        'build_rocketmq_kb.py',
        'query_rocketmq.py',
        'README_ROCKETMQ_KB.md',
        'rocketmq_kb_index.json',
        'index.json'
    ]

    for file in kb_files:
        source_path = os.path.join(source_dir, file)
        if os.path.exists(source_path):
            dest_path = os.path.join(structure['knowledge_base'], file)
            shutil.copy2(source_path, dest_path)
            print(f"📄 Copied: {file}")


def copy_tools_and_scripts(structure: dict, project_root: str):
    """Copy tools and scripts from the project."""

    # Copy chains directory
    chains_dir = os.path.join(project_root, 'chains')
    if os.path.exists(chains_dir):
        dest_chains = os.path.join(structure['tools'], 'chains')
        shutil.copytree(chains_dir, dest_chains, dirs_exist_ok=True)
        print(f"📁 Copied: chains/")

    # Copy skills directory
    skills_dir = os.path.join(project_root, 'skills')
    if os.path.exists(skills_dir):
        dest_skills = os.path.join(structure['tools'], 'skills')
        shutil.copytree(skills_dir, dest_skills, dirs_exist_ok=True)
        print(f"📁 Copied: skills/")

    # Copy tools directory
    tools_dir = os.path.join(project_root, 'tools')
    if os.path.exists(tools_dir):
        dest_tools = os.path.join(structure['tools'], 'rocketmq_tools')
        shutil.copytree(tools_dir, dest_tools, dirs_exist_ok=True)
        print(f"📁 Copied: tools/")


def copy_rocketmq_source(structure: dict, source_dir: str):
    """Copy RocketMQ source code with selective filtering."""

    rocketmq_src = os.path.join(source_dir, 'rocketmq_531')
    if not os.path.exists(rocketmq_src):
        print("⚠️  RocketMQ source directory not found")
        return

    # Copy only essential source code directories
    essential_dirs = [
        'client/src/main/java',
        'broker/src/main/java',
        'namesrv/src/main/java',
        'common/src/main/java',
        'remoting/src/main/java',
        'store/src/main/java'
    ]

    for rel_dir in essential_dirs:
        src_path = os.path.join(rocketmq_src, rel_dir)
        if os.path.exists(src_path):
            # Create corresponding destination path
            dest_rel = rel_dir.replace('src/main/java', '').strip('/')
            dest_path = os.path.join(structure['source_code'], dest_rel or 'core')

            # Copy Java source files only
            if os.path.exists(src_path):
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                print(f"📁 Copied: {rel_dir}")

    # Copy important configuration files
    config_files = [
        'pom.xml',
        'README.md',
        'LICENSE',
        'NOTICE'
    ]

    for config_file in config_files:
        src_path = os.path.join(rocketmq_src, config_file)
        if os.path.exists(src_path):
            dest_path = os.path.join(structure['source_code'], config_file)
            shutil.copy2(src_path, dest_path)
            print(f"📄 Copied: {config_file}")


def copy_documentation(structure: dict, source_dir: str):
    """Copy documentation files."""

    # Copy RocketMQ documentation
    rocketmq_docs = os.path.join(source_dir, 'rocketmq_531', 'docs')
    if os.path.exists(rocketmq_docs):
        dest_docs = os.path.join(structure['docs'], 'rocketmq')
        shutil.copytree(rocketmq_docs, dest_docs, dirs_exist_ok=True)
        print(f"📚 Copied: RocketMQ documentation")

    # Copy project documentation
    project_docs = [
        'README_ROCKETMQ_KB.md'
    ]

    for doc in project_docs:
        src_path = os.path.join(source_dir, doc)
        if os.path.exists(src_path):
            dest_path = os.path.join(structure['docs'], doc)
            shutil.copy2(src_path, dest_path)
            print(f"📄 Copied: {doc}")


def create_package_info(structure: dict):
    """Create package metadata and information files."""

    package_info = {
        'package_name': 'RocketMQ Knowledge Base',
        'version': '1.0.0',
        'created_date': datetime.now().isoformat(),
        'description': 'Complete RocketMQ knowledge base with source code, tools, and documentation',
        'contents': {
            'knowledge_base': 'Structured searchable index of RocketMQ Java source code',
            'source_code': 'Essential RocketMQ Java source files for reference',
            'tools': 'AI-powered troubleshooting tools and scripts',
            'docs': 'Documentation and usage guides',
            'examples': 'Usage examples and templates'
        },
        'statistics': {
            'java_files': 2056,
            'total_lines': 330245,
            'kb_documents': 5889,
            'unique_terms': 7704
        }
    }

    info_path = os.path.join(structure['root'], 'package-info.json')
    with open(info_path, 'w', encoding='utf-8') as f:
        json.dump(package_info, f, indent=2, ensure_ascii=False)

    # Create README
    readme_content = """# RocketMQ Knowledge Base Package

## Overview
This package contains a complete knowledge base of RocketMQ 5.3.1 Java source code, 
along with AI-powered tools for code search and troubleshooting.

## Contents

### 📚 Knowledge Base
- Structured index of RocketMQ Java source code
- Semantic search capabilities
- BM25-based relevance scoring

### 🔧 Tools & Scripts
- Interactive query tools
- Troubleshooting state machines
- Intent routing chains
- RocketMQ admin utilities

### 💻 Source Code
- Essential RocketMQ modules (client, broker, namesrv, etc.)
- Complete Java source files
- Build configuration files

### 📖 Documentation
- RocketMQ official documentation
- Usage guides and examples
- API references

## Quick Start

1. Extract the package to your desired location
2. Navigate to the `knowledge_base` directory
3. Run the query tool:
   ```bash
   python query_rocketmq.py
   ```

## Usage Examples

### Search for specific classes:
```bash
python query_rocketmq.py "DefaultMQPushConsumer"
```

### Search by concepts:
```bash
python query_rocketmq.py "message listener"
```

### Interactive mode:
```bash
python query_rocketmq.py
```

## System Requirements

- Python 3.8+
- 500MB+ disk space
- UTF-8 encoding support

## Support

For issues and questions, refer to the documentation in the `docs` directory.

---
*Package generated on {date}*
""".format(date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    readme_path = os.path.join(structure['root'], 'README.md')
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(readme_content)


def create_zip_package(structure: dict, output_path: str) -> str:
    """Create zip package from the structured directory."""

    zip_filename = f"rocketmq_knowledge_base_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = os.path.join(output_path, zip_filename)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(structure['root']):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, structure['root'])
                zipf.write(file_path, arcname)

    return zip_path


def calculate_package_size(temp_dir: str) -> tuple:
    """Calculate total size and file count of the package."""
    total_size = 0
    file_count = 0

    for root, dirs, files in os.walk(temp_dir):
        for file in files:
            file_path = os.path.join(root, file)
            total_size += os.path.getsize(file_path)
            file_count += 1

    return total_size, file_count


def main():
    """Main packaging function."""

    print("🚀 RocketMQ Knowledge Base Packaging Tool")
    print("=" * 60)

    # Get current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)

    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"📁 Temporary directory: {temp_dir}")

        # Create package structure
        print("\n📂 Creating package structure...")
        structure = create_package_structure(temp_dir)

        # Copy files
        print("\n📋 Copying knowledge base files...")
        copy_knowledge_base_files(structure, current_dir)

        print("\n🔧 Copying tools and scripts...")
        copy_tools_and_scripts(structure, project_root)

        print("\n💻 Copying RocketMQ source code...")
        copy_rocketmq_source(structure, current_dir)

        print("\n📖 Copying documentation...")
        copy_documentation(structure, current_dir)

        print("\n📊 Creating package metadata...")
        create_package_info(structure)

        # Calculate package statistics
        total_size, file_count = calculate_package_size(temp_dir)

        print(f"\n📈 Package Statistics:")
        print(f"   Files: {file_count:,}")
        print(f"   Size: {total_size / (1024 * 1024):.2f} MB")

        # Create zip package
        print("\n🗜️  Creating zip package...")
        output_dir = current_dir
        zip_path = create_zip_package(structure, output_dir)

        print(f"\n✅ Package created successfully!")
        print(f"📦 Location: {zip_path}")
        print(f"📏 Final size: {os.path.getsize(zip_path) / (1024 * 1024):.2f} MB")

        # Show package contents
        print("\n📋 Package Contents:")
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            for info in zipf.infolist()[:10]:  # Show first 10 files
                print(f"   {info.filename}")
            if len(zipf.infolist()) > 10:
                print(f"   ... and {len(zipf.infolist()) - 10} more files")

        print("\n🎉 Packaging complete!")
        print("💡 You can now distribute the zip file or extract it for local use.")


if __name__ == "__main__":
    main()
