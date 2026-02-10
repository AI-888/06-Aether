#!/bin/bash

# Aether项目打包脚本
# 打包根目录下的全部文件，包括依赖库分析

set -e

# 脚本配置
PROJECT_NAME="aether_project"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ZIP_NAME="${PROJECT_NAME}_${TIMESTAMP}.zip"
EXCLUDE_PATTERNS=(
    "*.git*"
    "*.venv*" 
    "*__pycache__*"
    "*.codebuddy*"
    "*.idea*"
    "*.DS_Store*"
    "*.pyc"
    "*.log"
    "*.tmp"
)

# 依赖配置文件名
DEPENDENCY_FILES=(
    "requirements.txt"
    "setup.py"
    "pyproject.toml"
    "Pipfile"
    "Pipfile.lock"
    "environment.yml"
)

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 显示帮助信息
show_help() {
    echo "Aether项目打包脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help     显示此帮助信息"
    echo "  -v, --verbose  详细输出模式"
    echo "  -q, --quiet    静默模式"
    echo "  -d, --deps     仅分析依赖关系（不打包）"
    echo ""
    echo "示例:"
    echo "  $0              # 默认打包"
    echo "  $0 -v           # 详细模式打包"
    echo "  $0 -d           # 仅分析依赖关系"
    echo ""
}

# 解析命令行参数
VERBOSE=false
QUIET=false
ANALYZE_DEPS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -q|--quiet)
            QUIET=true
            shift
            ;;
        -d|--deps)
            ANALYZE_DEPS=true
            shift
            ;;
        *)
            log_error "未知参数: $1"
            show_help
            exit 1
            ;;
    esac
done

# 检查zip命令是否可用
if ! command -v zip &> /dev/null; then
    log_error "zip命令未找到，请先安装zip工具"
    exit 1
fi

# 获取项目根目录
PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)

# 依赖分析函数
analyze_dependencies() {
    log_info "开始分析项目依赖关系..."
    
    # 查找依赖配置文件
    DEP_FILES_FOUND=()
    for dep_file in "${DEPENDENCY_FILES[@]}"; do
        if [ -f "$PROJECT_ROOT/$dep_file" ]; then
            DEP_FILES_FOUND+=("$dep_file")
        fi
    done
    
    if [ ${#DEP_FILES_FOUND[@]} -eq 0 ]; then
        log_warning "未找到依赖配置文件"
        return 1
    fi
    
    log_info "找到依赖配置文件: ${DEP_FILES_FOUND[*]}"
    
    # 分析requirements.txt
    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        log_info "分析requirements.txt..."
        echo "=== requirements.txt 依赖列表 ==="
        cat "$PROJECT_ROOT/requirements.txt" | grep -v "^#" | grep -v "^$" | while read line; do
            echo "  - $line"
        done
        echo ""
    fi
    
    # 生成依赖树分析（如果pipdeptree可用）
    if command -v pipdeptree &> /dev/null; then
        log_info "生成依赖树分析..."
        echo "=== 依赖树分析 ==="
        pipdeptree --warn silence 2>/dev/null | head -30
        echo ""
    else
        log_warning "pipdeptree未安装，无法生成完整依赖树"
        echo "安装建议: pip install pipdeptree"
        echo ""
    fi
    
    # 生成依赖打包建议
    log_info "依赖打包建议:"
    echo "1. 直接包含依赖配置文件: ${DEP_FILES_FOUND[*]}"
    echo "2. 建议在目标环境使用: pip install -r requirements.txt"
    echo "3. 对于离线部署，可下载依赖包: pip download -r requirements.txt -d ./dependencies"
    echo ""
    
    return 0
}

# 本地依赖配置文件打包
package_local_dependencies() {
    log_info "打包本地依赖配置文件..."
    
    # 检查并打包依赖配置文件
    local deps_found=false
    for dep_file in "${DEPENDENCY_FILES[@]}"; do
        if [ -f "$PROJECT_ROOT/$dep_file" ]; then
            log_info "找到依赖配置文件: $dep_file"
            deps_found=true
        fi
    done
    
    if [ "$deps_found" = true ]; then
        log_success "依赖配置文件已包含在打包中"
    else
        log_warning "未找到依赖配置文件，建议创建 requirements.txt"
    fi
}

# 如果仅分析依赖，则执行分析后退出
if [ "$ANALYZE_DEPS" = true ]; then
    analyze_dependencies
    exit 0
fi

log_info "开始打包Aether项目..."
log_info "项目根目录: $PROJECT_ROOT"
log_info "输出文件: $ZIP_NAME"

# 分析依赖关系
analyze_dependencies

# 打包本地依赖配置文件
package_local_dependencies

# 构建排除参数
EXCLUDE_ARGS=()
for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    EXCLUDE_ARGS+=(-x "$pattern")
done

# 计算文件数量
if [ "$VERBOSE" = true ]; then
    log_info "计算项目文件数量..."
    FILE_COUNT=$(find "$PROJECT_ROOT" -type f \( -name "*.git*" -o -name "*.venv*" -o -name "*__pycache__*" -o -name "*.codebuddy*" -o -name "*.idea*" -o -name "*.DS_Store*" -o -name "*.pyc" -o -name "*.log" -o -name "*.tmp" \) -prune -o -type f -print | wc -l)
    log_info "项目文件总数: $FILE_COUNT"
fi

# 执行打包
log_info "正在创建压缩包..."

if [ "$VERBOSE" = true ]; then
    cd "$PROJECT_ROOT"
    zip -r "$ZIP_NAME" . "${EXCLUDE_ARGS[@]}" -x "$ZIP_NAME"
elif [ "$QUIET" = true ]; then
    cd "$PROJECT_ROOT"
    zip -r "$ZIP_NAME" . "${EXCLUDE_ARGS[@]}" -x "$ZIP_NAME" > /dev/null 2>&1
else
    cd "$PROJECT_ROOT"
    zip -r "$ZIP_NAME" . "${EXCLUDE_ARGS[@]}" -x "$ZIP_NAME"
fi

# 检查打包结果
if [ $? -eq 0 ]; then
    # 获取压缩包信息
    ZIP_SIZE=$(du -h "$ZIP_NAME" | cut -f1)
    ZIP_FILES=$(unzip -l "$ZIP_NAME" | tail -1 | awk '{print $2}')
    
    log_success "打包完成!"
    log_info "压缩包: $ZIP_NAME"
    log_info "文件大小: $ZIP_SIZE"
    log_info "包含文件: $ZIP_FILES"
    
    # 显示包含的主要目录
    if [ "$VERBOSE" = true ]; then
        log_info "包含的主要目录:"
        unzip -l "$ZIP_NAME" | grep "/$" | head -10 | while read line; do
            echo "  $line"
        done
    fi
    
    # 创建打包信息文件
    cat > "packaging_info.txt" << EOF
打包时间: $(date)
项目名称: $PROJECT_NAME
压缩包: $ZIP_NAME
文件大小: $ZIP_SIZE
包含文件: $ZIP_FILES
排除模式: ${EXCLUDE_PATTERNS[*]}
依赖配置文件: ${DEP_FILES_FOUND[*]}

依赖安装说明:
1. 使用 pip install -r requirements.txt 安装依赖
2. 此包仅包含依赖配置文件，不包含虚拟环境
3. 建议在目标环境直接安装依赖
EOF
    
    log_info "打包信息已保存到: packaging_info.txt"
    
else
    log_error "打包失败!"
    exit 1
fi