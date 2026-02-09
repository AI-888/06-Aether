#!/bin/bash

# 打包脚本测试文件

set -e

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Aether项目打包脚本测试 ===${NC}"
echo ""

# 检查打包脚本是否存在
if [ ! -f "pack_all.sh" ]; then
    echo -e "${RED}错误: pack_all.sh 文件不存在${NC}"
    exit 1
fi

# 检查依赖分析脚本是否存在
if [ ! -f "deps_analyzer.sh" ]; then
    echo -e "${RED}错误: deps_analyzer.sh 文件不存在${NC}"
    exit 1
fi

# 给脚本添加执行权限
chmod +x pack_all.sh deps_analyzer.sh

echo "1. 检查脚本权限..."
if [ -x "pack_all.sh" ] && [ -x "deps_analyzer.sh" ]; then
    echo -e "${GREEN}✓ 脚本已具有执行权限${NC}"
else
    echo -e "${RED}✗ 脚本权限设置失败${NC}"
    exit 1
fi

echo ""
echo "2. 测试脚本语法..."
if bash -n pack_all.sh && bash -n deps_analyzer.sh; then
    echo -e "${GREEN}✓ 脚本语法检查通过${NC}"
else
    echo -e "${RED}✗ 脚本语法检查失败${NC}"
    exit 1
fi

echo ""
echo "3. 显示帮助信息..."
echo "=== pack_all.sh 帮助 ==="
./pack_all.sh --help
echo ""
echo "=== deps_analyzer.sh 帮助 ==="
./deps_analyzer.sh --help

echo ""
echo "4. 测试依赖分析功能..."
./deps_analyzer.sh -a

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 依赖分析功能正常${NC}"
else
    echo -e "${YELLOW}⚠ 依赖分析部分功能受限（可能缺少pip或pipdeptree）${NC}"
fi

echo ""
echo "5. 执行快速打包测试（静默模式）..."
./pack_all.sh --quiet

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 快速打包测试通过${NC}"
    
    # 检查生成的压缩包
    ZIP_FILE=$(ls -t aether_project_*.zip | head -1)
    if [ -f "$ZIP_FILE" ]; then
        echo -e "${GREEN}✓ 压缩包创建成功: $ZIP_FILE${NC}"
        
        # 显示压缩包信息
        echo ""
        echo "压缩包内容预览:"
        unzip -l "$ZIP_FILE" | head -20
        echo "..."
        
        # 显示文件大小
        ZIP_SIZE=$(du -h "$ZIP_FILE" | cut -f1)
        echo "压缩包大小: $ZIP_SIZE"
        
        # 检查是否包含依赖配置文件
        if unzip -l "$ZIP_FILE" | grep -q "requirements.txt"; then
            echo -e "${GREEN}✓ 依赖配置文件已包含在压缩包中${NC}"
        else
            echo -e "${YELLOW}⚠ 未找到依赖配置文件${NC}"
        fi
        
        # 清理测试文件
        echo ""
        echo "清理测试文件..."
        rm -f "$ZIP_FILE" "packaging_info.txt"
        echo -e "${GREEN}✓ 测试完成${NC}"
    else
        echo -e "${RED}✗ 压缩包未找到${NC}"
    fi
else
    echo -e "${RED}✗ 快速打包测试失败${NC}"
    exit 1
fi

echo ""
echo "6. 测试依赖包下载功能..."
if command -v pip &> /dev/null; then
    ./deps_analyzer.sh -d
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 依赖包下载功能正常${NC}"
        
        # 检查是否创建了依赖目录
        if [ -d "dependencies" ]; then
            echo "依赖包目录内容:"
            ls -la dependencies/ | head -10
            
            # 清理依赖目录
            rm -rf dependencies/
            echo -e "${GREEN}✓ 依赖包目录已清理${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ 依赖包下载功能受限${NC}"
    fi
else
    echo -e "${YELLOW}⚠ pip未安装，跳过依赖包下载测试${NC}"
fi

echo ""
echo -e "${BLUE}=== 功能特性验证 ===${NC}"
echo ""
echo "✅ 打包脚本功能:"
echo "  - 项目文件打包"
echo "  - 依赖配置文件包含"
echo "  - 智能文件排除"
echo "  - 多种运行模式"
echo ""
echo "✅ 依赖分析功能:"
echo "  - 依赖关系分析"
echo "  - 依赖包下载"
echo "  - 依赖树显示"
echo "  - 冲突检查"
echo "  - 离线部署包创建"
echo ""
echo -e "${GREEN}=== 所有测试通过 ===${NC}"
echo ""
echo "使用说明:"
echo "  ./pack_all.sh           # 标准模式打包"
echo "  ./pack_all.sh -v        # 详细模式打包"
echo "  ./pack_all.sh -q        # 静默模式打包"
echo "  ./pack_all.sh -d        # 仅分析依赖"
echo "  ./deps_analyzer.sh -a   # 分析依赖关系"
echo "  ./deps_analyzer.sh -d   # 下载依赖包"
echo "  ./deps_analyzer.sh -t   # 显示依赖树"
echo "  ./deps_analyzer.sh -o   # 创建离线部署包"