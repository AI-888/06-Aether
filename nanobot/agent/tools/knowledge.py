"""Knowledge base tools for storing and retrieving domain-specific knowledge."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from nanobot.agent.tools.base import Tool
from nanobot.config.loader import load_config
from nanobot.knowledge.store_factory import get_chroma_store
from nanobot.knowledge.store import DomainKnowledgeManager


def _create_chroma_store_with_config(workspace: Path):
    """创建或复用带有正确配置的 ChromaKnowledgeStore 实例."""
    config = load_config()
    return get_chroma_store(workspace, cfg=config)


class KnowledgeSearchTool(Tool):
    """Tool for searching knowledge base."""

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return "Search the local knowledge base for information about specific domains like RocketMQ, Kubernetes, etc. Use this when you need to recall stored knowledge or best practices."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Knowledge domain to search (e.g., 'rocketmq', 'kubernetes', 'github')",
                    "enum": ["rocketmq", "kubernetes", "github", "docker", "python", "javascript", "general"]
                },
                "query": {
                    "type": "string",
                    "description": "Search query keywords"
                },
                "category": {
                    "type": "string",
                    "description": "Category filter (e.g., 'troubleshooting', 'configuration', 'best_practices')",
                    "enum": ["troubleshooting", "configuration", "best_practices", "diagnostic_tools", "general"]
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to filter by"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10
                }
            },
            "required": ["domain", "query"]
        }

    async def execute(self, domain: str, query: str, category: Optional[str] = None,
                      tags: Optional[List[str]] = None, limit: int = 10) -> str:
        """Search knowledge base."""
        try:
            from loguru import logger
            config = load_config()

            logger.info(f"[KNOWLEDGE] 🔍 Search request:")
            logger.info(f"[KNOWLEDGE]   - Domain: {domain}")
            logger.info(f"[KNOWLEDGE]   - Query: {query}")
            logger.info(f"[KNOWLEDGE]   - Category: {category}")
            logger.info(f"[KNOWLEDGE]   - Tags: {tags}")
            logger.info(f"[KNOWLEDGE]   - Limit: {limit}")
            logger.info(f"[KNOWLEDGE]   - Workspace: {config.agents.defaults.workspace}")

            workspace = Path(config.agents.defaults.workspace)

            # Use ChromaKnowledgeStore for vector-based semantic search
            store = _create_chroma_store_with_config(workspace)

            # Search knowledge
            results = store.search_knowledge(
                query=query,
                domain=domain,
                category=category,
                tags=tags
            )

            logger.info(f"[KNOWLEDGE] 📊 Search results: {len(results)} items found")

            # Apply limit
            results = results[:limit]

            if not results:
                logger.info(f"[KNOWLEDGE] ⚠️  No knowledge found")
                return f"No knowledge found for domain '{domain}' with query '{query}'"

            # Log result titles
            for i, item in enumerate(results, 1):
                logger.info(f"[KNOWLEDGE]   {i}. {item.title} (score: {getattr(item, 'similarity_score', 'N/A')})")

            # Format results
            formatted_results = []
            for i, item in enumerate(results, 1):
                # 添加文档预览信息
                preview_info = ""
                preview_links = []
                
                # 检查文档链接
                if hasattr(item, 'source_url') and item.source_url:
                    preview_links.append(f"📄 文档链接: {item.source_url}")
                
                # 检查文件路径
                if hasattr(item, 'file_path') and item.file_path:
                    preview_links.append(f"📁 文件路径: {item.file_path}")
                
                # 检查是否可预览
                if hasattr(item, 'preview_available') and item.preview_available:
                    preview_links.append("🔍 支持预览")
                
                # 添加知识条目ID用于预览
                if hasattr(item, 'id') and item.id:
                    preview_links.append(f"🆔 条目ID: {item.id}")
                
                if preview_links:
                    preview_info = f"\n**预览信息**: {' | '.join(preview_links)}"
                
                formatted_results.append(f"""
### {i}. {item.title}
**Domain**: {item.domain} | **Category**: {item.category} | **Priority**: {item.priority}
**Tags**: {', '.join(item.tags)}
**Created**: {item.created_at[:10]}{preview_info}

{item.content}

---
""")

            result_text = f"Found {len(results)} knowledge items:\n" + "\n".join(formatted_results)
            logger.info(f"[KNOWLEDGE] ✅ Returning {len(result_text)} chars of formatted results")
            logger.info(f"[KNOWLEDGE] 📝 Returning {result_text}")
            return result_text

        except Exception as e:
            from loguru import logger
            logger.error(f"[KNOWLEDGE] ❌ Error searching knowledge base: {str(e)}")
            return f"Error searching knowledge base: {str(e)}"


class KnowledgeAddTool(Tool):
    """Tool for adding knowledge to the knowledge base."""

    @property
    def name(self) -> str:
        return "knowledge_add"

    @property
    def description(self) -> str:
        return "Add new knowledge to the local knowledge base. Use this to save important information, troubleshooting guides, best practices, or configuration details for future reference."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Knowledge domain (e.g., 'rocketmq', 'kubernetes', 'github')",
                    "enum": ["rocketmq", "kubernetes", "github", "docker", "python", "javascript", "general"]
                },
                "category": {
                    "type": "string",
                    "description": "Knowledge category",
                    "enum": ["troubleshooting", "configuration", "best_practices", "diagnostic_tools", "general"]
                },
                "title": {
                    "type": "string",
                    "description": "Title of the knowledge item"
                },
                "content": {
                    "type": "string",
                    "description": "Content of the knowledge item"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization"
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority level (1-5, higher is more important)",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1
                },
                "source_url": {
                    "type": "string",
                    "description": "Original document URL (optional)",
                    "default": ""
                },
                "file_path": {
                    "type": "string", 
                    "description": "Local file path (optional)",
                    "default": ""
                },
                "preview_available": {
                    "type": "boolean",
                    "description": "Whether document preview is available",
                    "default": True
                }
            },
            "required": ["domain", "category", "title", "content"]
        }

    async def execute(self, domain: str, category: str, title: str, content: str,
                      tags: Optional[List[str]] = None, priority: int = 1,
                      source_url: str = "", file_path: str = "", 
                      preview_available: bool = True) -> str:
        """Add knowledge to the knowledge base."""
        try:
            config = load_config()
            workspace = Path(config.agents.defaults.workspace)

            # Use ChromaKnowledgeStore for vector-based knowledge storage
            store = _create_chroma_store_with_config(workspace)

            item_id = store.add_knowledge(
                domain=domain,
                category=category,
                title=title,
                content=content,
                tags=tags,
                priority=priority,
                source_url=source_url,
                file_path=file_path,
                preview_available=preview_available
            )

            return f"Successfully added knowledge item '{title}' with ID: {item_id}"

        except Exception as e:
            return f"Error adding knowledge: {str(e)}"


class DomainKnowledgeTool(Tool):
    """Specialized tool for domain-specific knowledge management."""

    @property
    def name(self) -> str:
        return "knowledge_domain"

    @property
    def description(self) -> str:
        return "Specialized domain knowledge management tool. Use this for domain-specific troubleshooting, configuration guides, best practices, and diagnostic checkers."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "enum": ["search_troubleshooting", "search_configuration", "search_checkers", "add_troubleshooting",
                             "add_configuration", "add_checker", "list_checkers"]
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for search actions)"
                },
                "title": {
                    "type": "string",
                    "description": "Title for new knowledge item (for add actions)"
                },
                "content": {
                    "type": "string",
                    "description": "Content for new knowledge item (for add actions)"
                },
                "checker_name": {
                    "type": "string",
                    "description": "Checker name (for add_checker action)"
                },
                "description": {
                    "type": "string",
                    "description": "Checker description (for add_checker action)"
                },
                "usage": {
                    "type": "string",
                    "description": "Checker usage scenario (for add_checker action)"
                },
                "admin_api": {
                    "type": "string",
                    "description": "Admin API method (for add_checker action)"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization"
                }
            },
            "required": ["action"]
        }

    async def execute(self, action: str, query: Optional[str] = None, title: Optional[str] = None,
                      content: Optional[str] = None, checker_name: Optional[str] = None,
                      description: Optional[str] = None, usage: Optional[str] = None,
                      admin_api: Optional[str] = None, tags: Optional[List[str]] = None) -> str:
        """Execute domain knowledge action."""
        try:
            config = load_config()
            workspace = Path(config.agents.defaults.workspace)

            # Use ChromaKnowledgeStore for vector-based knowledge storage
            store = _create_chroma_store_with_config(workspace)
            domain_manager = DomainKnowledgeManager(store, "rocketmq")

            if action == "search_troubleshooting":
                results = domain_manager.search_troubleshooting(query=query, tags=tags)
                return self._format_results("RocketMQ Troubleshooting", results)

            elif action == "search_configuration":
                results = domain_manager.search_configuration(query=query, tags=tags)
                return self._format_results("RocketMQ Configuration", results)

            elif action == "search_checkers":
                results = domain_manager.search_checkers(query=query)
                return self._format_results("RocketMQ Diagnostic Checkers", results)

            elif action == "list_checkers":
                results = domain_manager.get_all_checkers()
                return self._format_results("All RocketMQ Diagnostic Checkers", results)

            elif action == "add_troubleshooting":
                if not title or not content:
                    return "Error: title and content are required for add_troubleshooting"

                item_id = domain_manager.add_troubleshooting_guide(title, content, tags)
                return f"Added RocketMQ troubleshooting guide: {title} (ID: {item_id})"

            elif action == "add_configuration":
                if not title or not content:
                    return "Error: title and content are required for add_configuration"

                item_id = domain_manager.add_configuration_guide(title, content, tags)
                return f"Added RocketMQ configuration guide: {title} (ID: {item_id})"

            elif action == "add_checker":
                if not checker_name or not description or not usage:
                    return "Error: checker_name, description, and usage are required for add_checker"

                item_id = domain_manager.add_checker_info(checker_name, description, usage, admin_api, tags)
                return f"Added RocketMQ checker info: {checker_name} (ID: {item_id})"

            else:
                return f"Unknown action: {action}"

        except Exception as e:
            return f"Error performing domain knowledge action: {str(e)}"

    def _format_results(self, title: str, results: List) -> str:
        """Format search results."""
        if not results:
            return f"No {title.lower()} found"

        formatted = [f"# {title}", f"Found {len(results)} items:", ""]

        for i, item in enumerate(results, 1):
            formatted.append(f"## {i}. {item.title}")
            formatted.append(f"**Category**: {item.category}")
            formatted.append(f"**Tags**: {', '.join(item.tags)}")
            formatted.append(f"**Priority**: {item.priority}")
            formatted.append(f"**Created**: {item.created_at[:10]}")
            formatted.append("")
            formatted.append(item.content)
            formatted.append("---")
            formatted.append("")

        return "\n".join(formatted)


class KnowledgeExportTool(Tool):
    """Tool for exporting knowledge base."""

    @property
    def name(self) -> str:
        return "knowledge_export"

    @property
    def description(self) -> str:
        return "Export knowledge base data for backup or migration purposes."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Specific domain to export (optional, exports all if not specified)",
                    "enum": ["rocketmq", "kubernetes", "github", "docker", "python", "javascript", "general"]
                },
                "format": {
                    "type": "string",
                    "description": "Export format",
                    "enum": ["json", "markdown"],
                    "default": "json"
                }
            }
        }

    async def execute(self, domain: Optional[str] = None, format: str = "json") -> str:
        """Export knowledge base."""
        try:
            config = load_config()
            workspace = Path(config.agents.defaults.workspace)

            # Use ChromaKnowledgeStore for vector-based knowledge storage
            store = _create_chroma_store_with_config(workspace)

            if format == "json":
                export_data = store.export_knowledge(domain=domain)
                return json.dumps(export_data, indent=2, ensure_ascii=False)

            elif format == "markdown":
                results = store.search_knowledge(domain=domain)

                if not results:
                    return "No knowledge items found to export"

                markdown = [f"# Knowledge Base Export", f"**Domain**: {domain or 'All'}",
                            f"**Export Date**: {datetime.now().isoformat()}", ""]

                for item in results:
                    markdown.append(f"## {item.title}")
                    markdown.append(
                        f"**Domain**: {item.domain} | **Category**: {item.category} | **Priority**: {item.priority}")
                    markdown.append(f"**Tags**: {', '.join(item.tags)}")
                    markdown.append(f"**Created**: {item.created_at}")
                    markdown.append("")
                    markdown.append(item.content)
                    markdown.append("---")
                    markdown.append("")

                return "\n".join(markdown)

            else:
                return f"Unsupported format: {format}"

        except Exception as e:
            return f"Error exporting knowledge base: {str(e)}"
