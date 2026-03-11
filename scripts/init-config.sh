#!/bin/sh
# init-config.sh
# 将环境变量渲染到 config-template.json，生成 config.json

set -e

TEMPLATE_FILE="${TEMPLATE_FILE:-/app/config-template.json}"
OUTPUT_FILE="${OUTPUT_FILE:-/root/.nanobot/config.json}"
OUTPUT_DIR=$(dirname "$OUTPUT_FILE")

echo "=== Aether 配置初始化 ==="
echo "模板文件: $TEMPLATE_FILE"
echo "输出文件: $OUTPUT_FILE"

# 确保输出目录存在
mkdir -p "$OUTPUT_DIR"

# 检查模板文件是否存在
if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "❌ 错误: 模板文件不存在: $TEMPLATE_FILE"
    exit 1
fi

# 设置默认值（如果环境变量未设置）
OLLAMA_API_BASE_HOST_PORT="${OLLAMA_API_BASE_HOST_PORT:-http://localhost:11434}"
SLM_MODEL_NAME="${SLM_MODEL_NAME:-qwen2.5:7b}"
AETHER_HOME="${AETHER_HOME:-/root/.nanobot}"
EMBEDDING_MODEL_NAME="${EMBEDDING_MODEL_NAME:-bge-small-en-v1.5}"
ROCKETMQ_MCP_SERVER_URL="${ROCKETMQ_MCP_SERVER_URL:-http://rocketmq-mcp-server:8080}"
RERANK_MODEL_NAME="${RERANK_MODEL_NAME:-bge-reranker-v2-m3}"

echo "当前环境变量:"
echo "  OLLAMA_API_BASE_HOST_PORT = $OLLAMA_API_BASE_HOST_PORT"
echo "  SLM_MODEL_NAME            = $SLM_MODEL_NAME"
echo "  AETHER_HOME               = $AETHER_HOME"
echo "  EMBEDDING_MODEL_NAME      = $EMBEDDING_MODEL_NAME"
echo "  ROCKETMQ_MCP_SERVER_URL   = $ROCKETMQ_MCP_SERVER_URL"
echo "  RERANK_MODEL_NAME         = $RERANK_MODEL_NAME"

# 使用 sed 将模板中的 ${VAR} 替换为实际环境变量值
# 注意：sed 中需要对特殊字符（如 / : .）进行转义
escape_sed() {
    echo "$1" | sed 's/[\/&]/\\&/g'
}

OLLAMA_ESCAPED=$(escape_sed "$OLLAMA_API_BASE_HOST_PORT")
SLM_ESCAPED=$(escape_sed "$SLM_MODEL_NAME")
AETHER_ESCAPED=$(escape_sed "$AETHER_HOME")
EMBEDDING_ESCAPED=$(escape_sed "$EMBEDDING_MODEL_NAME")
ROCKETMQ_ESCAPED=$(escape_sed "$ROCKETMQ_MCP_SERVER_URL")
RERANK_ESCAPED=$(escape_sed "$RERANK_MODEL_NAME")

sed \
    -e "s/\${OLLAMA_API_BASE_HOST_PORT}/$OLLAMA_ESCAPED/g" \
    -e "s/\${SLM_MODEL_NAME}/$SLM_ESCAPED/g" \
    -e "s/\${AETHER_HOME}/$AETHER_ESCAPED/g" \
    -e "s/\${EMBEDDING_MODEL_NAME}/$EMBEDDING_ESCAPED/g" \
    -e "s/\${ROCKETMQ_MCP_SERVER_URL}/$ROCKETMQ_ESCAPED/g" \
    -e "s/\${RERANK_MODEL_NAME}/$RERANK_ESCAPED/g" \
    "$TEMPLATE_FILE" > "$OUTPUT_FILE"

echo "✅ 配置文件生成成功: $OUTPUT_FILE"
echo "--- 生成内容 ---"
cat "$OUTPUT_FILE"
echo "--- 配置初始化完成 ---"
