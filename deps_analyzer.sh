#!/bin/bash

# Aether项目依赖分析脚本
# 深度分析项目依赖关系，支持依赖包下载和离线部署

set -e

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
    echo "Aether项目依赖分析脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help         显示此帮助信息"
    echo "  -a, --analyze      分析依赖关系（默认）"
    echo "  -d, --download     下载依赖包到本地"
    echo "  -t, --tree         显示依赖树"
    echo "  -c, --check        检查依赖冲突"
    echo "  -o, --offline      创建离线部署包"
    echo "  -v, --verbose      详细输出模式"
    echo ""
    echo "示例:"
    echo "  $0 -a              # 分析依赖关系"
    echo "  $0 -d              # 下载依赖包"
    echo "  $0 -t              # 显示依赖树"
    echo "  $0 -o              # 创建离线部署包"
    echo ""
}

# 获取项目根目录
PROJECT_ROOT=$(cd "$(dirname "$0")" && pwd)

# 依赖配置文件
DEPENDENCY_FILES=(
    "requirements.txt"
    "setup.py"
    "pyproject.toml"
    "Pipfile"
    "Pipfile.lock"
    "environment.yml"
)

# 解析命令行参数
ANALYZE=false
DOWNLOAD=false
TREE=false
CHECK=false
OFFLINE=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -a|--analyze)
            ANALYZE=true
            shift
            ;;
        -d|--download)
            DOWNLOAD=true
            shift
            ;;
        -t|--tree)
            TREE=true
            shift
            ;;
        -c|--check)
            CHECK=true
            shift
            ;;
        -o|--offline)
            OFFLINE=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        *)
            log_error "未知参数: $1"
            show_help
            exit 1
            ;;
    esac
done

# 如果没有指定任何操作，默认分析
if [ "$ANALYZE" = false ] && [ "$DOWNLOAD" = false ] && [ "$TREE" = false ] && [ "$CHECK" = false ] && [ "$OFFLINE" = false ]; then
    ANALYZE=true
fi

# 检查依赖配置文件
detect_dependency_files() {
    local found_files=()
    for dep_file in "${DEPENDENCY_FILES[@]}"; do
        if [ -f "$PROJECT_ROOT/$dep_file" ]; then
            found_files+=("$dep_file")
        fi
    done
    echo "${found_files[@]}"
}

# 分析依赖关系
analyze_dependencies() {
    log_info "=== 依赖关系分析 ==="
    
    local dep_files=$(detect_dependency_files)
    if [ -z "$dep_files" ]; then
        log_error "未找到依赖配置文件"
        return 1
    fi
    
    log_info "找到依赖配置文件: $dep_files"
    echo ""
    
    # 分析requirements.txt
    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        echo "=== requirements.txt 依赖列表 ==="
        cat "$PROJECT_ROOT/requirements.txt" | grep -v "^#" | grep -v "^$" | while read line; do
            echo "  📦 $line"
        done
        echo ""
    fi
    
    # 检查pip和pipdeptree
    if ! command -v pip &> /dev/null; then
        log_warning "pip未安装，无法进行深度依赖分析"
        return 0
    fi
    
    # 显示已安装包
    if [ "$VERBOSE" = true ]; then
        echo "=== 当前环境已安装包 ==="
        pip list --format=columns | head -20
        echo "..."
        echo ""
    fi
    
    # 依赖树分析
    if command -v pipdeptree &> /dev/null; then
        echo "=== 依赖树分析 ==="
        pipdeptree --warn silence 2>/dev/null | head -40
        echo "..."
        echo ""
    else
        log_warning "pipdeptree未安装，无法显示依赖树"
        echo "安装命令: pip install pipdeptree"
        echo ""
    fi
    
    return 0
}

# 下载依赖包
download_dependencies() {
    log_info "=== 下载依赖包 ==="
    
    if [ ! -f "$PROJECT_ROOT/requirements.txt" ]; then
        log_error "requirements.txt 文件不存在"
        return 1
    fi
    
    local deps_dir="$PROJECT_ROOT/dependencies"
    mkdir -p "$deps_dir"
    
    log_info "下载依赖包到: $deps_dir/"
    
    # 下载依赖包
    if pip download -r "$PROJECT_ROOT/requirements.txt" -d "$deps_dir" 2>/dev/null; then
        log_success "依赖包下载完成"
        
        # 统计下载的包
        local package_count=$(ls "$deps_dir"/*.whl "$deps_dir"/*.tar.gz 2>/dev/null | wc -l)
        log_info "下载包数量: $package_count"
        
        # 显示下载的包列表
        if [ "$VERBOSE" = true ]; then
            echo "=== 下载的依赖包 ==="
            ls "$deps_dir"/*.whl "$deps_dir"/*.tar.gz 2>/dev/null | xargs -n1 basename | while read pkg; do
                echo "  📦 $pkg"
            done
        fi
        
        # 创建安装脚本
        cat > "$deps_dir/install_deps.sh" << 'EOF'
#!/bin/bash
# 离线安装依赖脚本

echo "开始离线安装依赖..."

# 检查pip是否可用
if ! command -v pip &> /dev/null; then
    echo "错误: pip未安装"
    exit 1
fi

# 安装所有下载的包
for pkg in *.whl *.tar.gz; do
    if [ -f "$pkg" ]; then
        echo "安装: $pkg"
        pip install "$pkg"
    fi
done

echo "依赖安装完成!"
EOF
        
        chmod +x "$deps_dir/install_deps.sh"
        log_info "创建离线安装脚本: $deps_dir/install_deps.sh"
        
    else
        log_error "依赖包下载失败"
        return 1
    fi
    
    return 0
}

# 显示依赖树
show_dependency_tree() {
    log_info "=== 依赖树显示 ==="
    
    if ! command -v pipdeptree &> /dev/null; then
        log_error "pipdeptree未安装"
        echo "安装命令: pip install pipdeptree"
        return 1
    fi
    
    pipdeptree
    
    return 0
}

# 检查依赖冲突
check_dependency_conflicts() {
    log_info "=== 依赖冲突检查 ==="
    
    if ! command -v pipdeptree &> /dev/null; then
        log_error "pipdeptree未安装"
        return 1
    fi
    
    # 检查冲突
    local conflicts=$(pipdeptree --warn silence 2>&1 | grep -i "conflict\|incompatible\|warning" || true)
    
    if [ -n "$conflicts" ]; then
        log_warning "发现依赖冲突:"
        echo "$conflicts"
        echo ""
    else
        log_success "未发现依赖冲突"
    fi
    
    return 0
}

# 创建离线部署包
create_offline_package() {
    log_info "=== 创建离线部署包 ==="
    
    local offline_dir="$PROJECT_ROOT/offline_deployment"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local offline_package="offline_deployment_${timestamp}.tar.gz"
    
    # 创建离线部署目录结构
    mkdir -p "$offline_dir/project"
    mkdir -p "$offline_dir/dependencies"
    
    # 复制项目文件（排除不需要的文件）
    log_info "复制项目文件..."
    rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='.DS_Store' --exclude='.idea' "$PROJECT_ROOT/" "$offline_dir/project/" 2>/dev/null || true
    
    # 下载依赖包
    log_info "下载依赖包..."
    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        pip download -r "$PROJECT_ROOT/requirements.txt" -d "$offline_dir/dependencies" 2>/dev/null || log_warning "部分依赖包下载失败"
    fi
    
    # 创建部署脚本
    cat > "$offline_dir/deploy.sh" << 'EOF'
#!/bin/bash
# Aether项目离线部署脚本

echo "=== Aether项目离线部署 ==="

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "错误: Python3未安装"
    exit 1
fi

# 创建虚拟环境
echo "创建虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 安装依赖
echo "安装依赖..."
if [ -d "dependencies" ] && [ "$(ls dependencies/*.whl dependencies/*.tar.gz 2>/dev/null)" ]; then
    # 离线安装
    for pkg in dependencies/*.whl dependencies/*.tar.gz; do
        if [ -f "$pkg" ]; then
            pip install "$pkg"
        fi
    done
else
    # 在线安装
    if [ -f "project/requirements.txt" ]; then
        pip install -r project/requirements.txt
    fi
fi

echo "部署完成!"
echo "启动命令: source venv/bin/activate && cd project && python main.py"
EOF
    
    chmod +x "$offline_dir/deploy.sh"
    
    # 创建README
    cat > "$offline_dir/README.md" << EOF
# Aether项目离线部署包

## 部署说明

1. 解压此包: tar -xzf $offline_package
2. 进入目录: cd offline_deployment
3. 执行部署: ./deploy.sh

## 包含内容

- 📁 project/ - 项目源代码
- 📁 dependencies/ - 依赖包（可选）
- 📄 deploy.sh - 部署脚本

## 注意事项

- 确保目标机器已安装Python3
- 如需网络安装，请确保网络连接
- 部署完成后使用虚拟环境运行项目

生成时间: $(date)
EOF
    
    # 打包离线部署包
    log_info "创建离线部署包..."
    tar -czf "$offline_package" -C "$PROJECT_ROOT" "offline_deployment"
    
    # 清理临时目录
    rm -rf "$offline_dir"
    
    log_success "离线部署包创建完成: $offline_package"
    
    # 显示包信息
    local package_size=$(du -h "$offline_package" | cut -f1)
    log_info "包大小: $package_size"
    
    return 0
}

# 主执行逻辑
main() {
    echo -e "${GREEN}=== Aether项目依赖分析工具 ===${NC}"
    echo ""
    
    if [ "$ANALYZE" = true ]; then
        analyze_dependencies
    fi
    
    if [ "$DOWNLOAD" = true ]; then
        download_dependencies
    fi
    
    if [ "$TREE" = true ]; then
        show_dependency_tree
    fi
    
    if [ "$CHECK" = true ]; then
        check_dependency_conflicts
    fi
    
    if [ "$OFFLINE" = true ]; then
        create_offline_package
    fi
    
    echo -e "${GREEN}=== 分析完成 ===${NC}"
}

# 执行主函数
main "$@"