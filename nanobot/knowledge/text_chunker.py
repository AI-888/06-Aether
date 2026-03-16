"""Text chunking for long documents."""

import logging
from typing import Any, Dict, List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

class TextChunker:
    """文本分块器，将长文本分割为语义块."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200, 
                 smart_chunking: bool = False, preserve_structure: bool = False):
        """初始化分块器.
        
        Args:
            chunk_size: 块大小（字符数）
            chunk_overlap: 块重叠大小（字符数）
            smart_chunking: 启用智能分割（已废弃，保留兼容性）
            preserve_structure: 保持文档结构（已废弃，保留兼容性）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.smart_chunking = False  # 强制关闭智能分块
        self.preserve_structure = False  # 强制关闭结构保持
        self.splitter = None
        self._init_splitter()

    def _init_splitter(self) -> None:
        """初始化文本分割器，配置中文友好的分隔符."""
        # 统一使用标准分割，聚焦分隔符分割
        separators = [
            "CHUNK_BOUNDARY",  # 手动分块标记（最高优先级）
            "\n\n```",  # 代码块
            "\n\n",  # 段落分隔符
            "\n```",  # 代码块（无前导换行）
            "。",  # 中文句号
            "！",  # 中文感叹号
            "？",  # 中文问号
            "；",  # 中文分号
            ".",  # 英文句号
            "!",  # 英文感叹号
            "?",  # 英文问号
            ";",  # 英文分号
            "，",  # 中文逗号
            ",",  # 英文逗号
            " ",  # 空格
            "",  # 字符级别分割
        ]
            
        # 使用递归字符分割策略，优先在段落、句子边界处分块
        # 分隔符按优先级排序：手动标记 > 代码块 > 段落 > 句子 > 标点 > 空格 > 字符
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=separators,
            keep_separator=True,  # 保留分隔符
            length_function=len,
        )
        
        logger.info(f"分隔符分割器初始化完成: chunk_size={self.chunk_size}, chunk_overlap={self.chunk_overlap}")
        logger.info(f"分隔符优先级: CHUNK_BOUNDARY > 代码块 > 段落 > 句子 > 标点")

    def chunk_text(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """分块文本并保留元数据.
        
        Args:
            text: 输入文本
            metadata: 元数据（id, domain, category, title, tags 等）
            
        Returns:
            分块结果列表，每个元素包含 text 和 metadata
            如果文本长度不超过 chunk_size，返回单个块
        """
        if not text or not text.strip():
            logger.warning("尝试分块空文本，返回空列表")
            return []

        # 检测是否包含手动分块标记
        chunk_markers = ["CHUNK_BOUNDARY"]
        has_chunk_marker = any(marker in text for marker in chunk_markers)
        
        if has_chunk_marker:
            logger.info(f"检测到手动分块标记，文档: {metadata.get('title', 'Unknown')}")
            for marker in chunk_markers:
                if marker in text:
                    chunk_count = text.count(marker)
                    logger.info(f"{marker} 标记数量: {chunk_count}")
                    break

        # 如果文本长度不超过 chunk_size，且不含手动分块标记，不需要分块
        if len(text) <= self.chunk_size and not has_chunk_marker:
            logger.debug(f"文本长度 {len(text)} 不超过 chunk_size {self.chunk_size}，不分块")
            return [{
                "text": text,
                "metadata": {
                    **metadata,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            }]

        # 使用标准分块，不再使用智能分块
        try:
            chunks = self.splitter.split_text(text)
                
            logger.info(f"文本分块完成: 原始长度={len(text)}, 分块数={len(chunks)}")

            # 打印原始分块的详细信息
            logger.debug("=== 原始分块详情 ===")
            for i, chunk in enumerate(chunks):
                chunk_preview = chunk.replace('\n', '\\n')[:100]
                logger.debug(f"原始Chunk {i+1}: 长度={len(chunk)}, 预览='{chunk_preview}...'")
                for marker in chunk_markers:
                    if marker in chunk:
                        logger.debug(f"  ⚠️ Chunk {i+1} 包含{marker}标记")
                        break

            # 为每个分块添加元数据
            result = []
            for i, chunk in enumerate(chunks):
                # 如果chunk包含CHUNK_BOUNDARY标记，即使内容较少也保留
                has_marker = any(marker in chunk for marker in chunk_markers)
                if has_marker:
                    result.append({
                        "text": chunk,
                        "metadata": {
                            **metadata,
                            "chunk_index": len(result),
                            "total_chunks": len(chunks),
                        }
                    })
                    continue
                    
                # 对于不包含CHUNK_BOUNDARY标记的chunk，过滤掉内容太少的
                clean_chunk = chunk
                for marker in chunk_markers:
                    clean_chunk = clean_chunk.replace(marker, "")
                clean_chunk = clean_chunk.strip()
                
                if len(clean_chunk) < 10:  # 过滤掉内容太少的chunk
                    logger.debug(f"跳过内容过少的chunk {i+1}: {len(clean_chunk)} 字符, 内容='{clean_chunk}'")
                    continue
                    
                result.append({
                    "text": chunk,
                    "metadata": {
                        **metadata,
                        "chunk_index": len(result),
                        "total_chunks": len(chunks),
                    }
                })

            # 更新总chunk数量
            for item in result:
                item["metadata"]["total_chunks"] = len(result)

            # 打印最终分块的详细信息
            logger.debug("=== 最终分块详情 ===")
            for i, item in enumerate(result):
                chunk = item["text"]
                chunk_preview = chunk.replace('\n', '\\n')
                has_boundary = any(marker in chunk for marker in chunk_markers)
                logger.debug(f"最终Chunk {i+1}: 长度={len(chunk)}, BOUNDARY={has_boundary}")
                logger.debug(f"  全部chunk内容: {chunk_preview}")
                
                # 如果chunk包含标题，特别标注
                if any(marker in chunk for marker in ["####", "###", "**步骤"]):
                    titles = []
                    if "####" in chunk:
                        titles.append("四级标题")
                    if "###" in chunk:
                        titles.append("三级标题")
                    if "**步骤" in chunk:
                        titles.append("步骤标记")
                    logger.debug(f"  📋 包含结构: {', '.join(titles)}")

            logger.info(f"过滤后的分块数量: {len(result)} (原始: {len(chunks)})")
            return result

        except Exception as e:
            logger.error(f"文本分块失败: {str(e)}", exc_info=True)
            # 分块失败时，返回整个文本作为单个块
            logger.warning("分块失败，返回整个文本作为单个块")
            return [{
                "text": text,
                "metadata": {
                    **metadata,
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            }]