#!/bin/bash

# Docker镜像构建脚本
# 支持设置镜像仓库和版本

# 默认配置
DEFAULT_REGISTRY="docker.io"
DEFAULT_REPOSITORY="youlixishi/aether"
DEFAULT_TAG="latest"

# 使用帮助函数
usage() {
    echo "用法: $0 [选项]"
    echo "选项:"
    echo "  -r, --registry REGISTRY    设置镜像仓库 (默认: $DEFAULT_REGISTRY)"
    echo "  -p, --repository REPO      设置镜像仓库路径 (默认: $DEFAULT_REPOSITORY)"
    echo "  -t, --tag TAG              设置镜像标签 (默认: 当前时间戳)"
    echo "  -b, --build-only           仅构建镜像，不推送"
    echo "  -h, --help                 显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 -r docker.io -p myorg/aether -t v1.0.0"
    echo "  $0 --registry ghcr.io --repository youlixishi/aether"
}

# 解析命令行参数
REGISTRY="$DEFAULT_REGISTRY"
REPOSITORY="$DEFAULT_REPOSITORY"
TAG=""
BUILD_ONLY=true

while [[ $# -gt 0 ]]; do
    case $1 in
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -p|--repository)
            REPOSITORY="$2"
            shift 2
            ;;
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -b|--build-only)
            BUILD_ONLY=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "错误: 未知选项 $1"
            usage
            exit 1
            ;;
    esac
done

# 如果没有指定tag，使用当前时间的秒级时间戳，并格式化为每3位添加横线分割
if [ -z "$TAG" ]; then
    # 获取当前时间戳
    TIMESTAMP=$(date +%s)
    
    # 纯shell函数：每3位数字添加横线分割（兼容macOS和Linux）
    format_timestamp() {
        local input="$1"
        local result=""
        local count=0
        
        # 遍历每个字符
        while [ ${#input} -gt 0 ]; do
            # 每次取3个字符
            local chunk="${input:0:3}"
            # 更新剩余字符
            input="${input:3}"
            
            # 添加横线（除非是第一次迭代）
            if [ ${#result} -gt 0 ]; then
                result="${result}-${chunk}"
            else
                result="${chunk}"
            fi
        done
        
        echo "$result"
    }
    
    # 调用格式化函数
    TAG=$(format_timestamp "$TIMESTAMP")
fi

# 构建完整的镜像名称
IMAGE_NAME="${REGISTRY}/${REPOSITORY}:${TAG}"

echo "=== Docker镜像构建配置 ==="
echo "镜像仓库: $REGISTRY"
echo "仓库路径: $REPOSITORY"
echo "镜像标签: $TAG"
echo "完整镜像名: $IMAGE_NAME"
echo "仅构建模式: $BUILD_ONLY"
echo "=========================="

# 检查Docker是否可用
if ! command -v docker &> /dev/null; then
    echo "错误: Docker未安装或不在PATH中"
    exit 1
fi

# 构建镜像
echo "开始构建Docker镜像..."
# 检测操作系统，在macOS上去除--progress=plain参数
if [ "$(uname)" = "Darwin" ]; then
    echo "检测到macOS系统，移除--progress=plain参数"
    if docker build -t "$IMAGE_NAME" .; then
        echo "✅ Docker镜像构建成功: $IMAGE_NAME"
    else
        echo "❌ Docker镜像构建失败"
        exit 1
    fi
else
    echo "检测到Linux系统，使用--progress=plain参数"
    if docker build --progress=plain -t "$IMAGE_NAME" .; then
        echo "✅ Docker镜像构建成功: $IMAGE_NAME"
    else
        echo "❌ Docker镜像构建失败"
        exit 1
    fi
fi

# 如果不在仅构建模式，则推送镜像
if [ "$BUILD_ONLY" = false ]; then
    echo "开始推送镜像到仓库..."
    if docker push "$IMAGE_NAME"; then
        echo "✅ 镜像推送成功: $IMAGE_NAME"
    else
        echo "❌ 镜像推送失败，请检查仓库权限和网络连接"
        exit 1
    fi
else
    echo "跳过镜像推送（构建模式）"
fi

echo "=== 构建完成 ==="
echo "镜像: $IMAGE_NAME"
echo "大小: $(docker images "$IMAGE_NAME" --format "table {{.Size}}" | tail -n1)"

# 显示使用示例
echo ""
echo "使用示例:"
echo " docker run -d --rm --name aether-bot -p 8001:8001 -v /data3/.nanobot:/root/.nanobot $IMAGE_NAME"
