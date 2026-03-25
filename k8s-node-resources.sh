#!/bin/bash
# =============================================================================
# K8s Node 资源使用分析脚本
# 功能：分析集群中每个 Node 的 CPU/内存 资源使用情况，按剩余量从高到低排序
# =============================================================================

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── 参数解析 ─────────────────────────────────────────────────────────────────
TOP_N=10        # 默认展示 top 10
CONCURRENCY=10  # 默认并发数 10

usage() {
    echo "用法: $0 [-n <数量>] [-c <并发数>] [-a] [-h]"
    echo ""
    echo "选项:"
    echo "  -n, --top <数量>         展示资源剩余最多的前 N 个 Node（默认: 10）"
    echo "  -c, --concurrency <数量> 并发采集的线程数（默认: 10）"
    echo "  -a, --all                展示所有 Node（不限制数量）"
    echo "  -h, --help               显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                # 展示 top 10，并发 10"
    echo "  $0 -n 5           # 展示 top 5"
    echo "  $0 -n 20 -c 20    # 展示 top 20，并发 20"
    echo "  $0 -a -c 30       # 展示全部，并发 30"
    exit 0
}

SHOW_ALL=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--top)
            if [[ -z "${2:-}" || ! "$2" =~ ^[0-9]+$ ]]; then
                echo -e "${RED}错误: -n/--top 需要一个正整数参数${NC}"
                exit 1
            fi
            TOP_N="$2"
            shift 2
            ;;
        -c|--concurrency)
            if [[ -z "${2:-}" || ! "$2" =~ ^[0-9]+$ || "$2" -eq 0 ]]; then
                echo -e "${RED}错误: -c/--concurrency 需要一个大于 0 的正整数${NC}"
                exit 1
            fi
            CONCURRENCY="$2"
            shift 2
            ;;
        -a|--all)
            SHOW_ALL=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            usage
            ;;
    esac
done

# ── Step 1: 获取全部 Node ────────────────────────────────────────────────────
echo -e "${CYAN}${BOLD}=== K8s Node 资源使用分析 ===${NC}"
echo ""

NODES=$(kubectl get nodes --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null)
if [ -z "$NODES" ]; then
    echo -e "${RED}错误: 无法获取 Node 列表，请检查 kubectl 连接和权限${NC}"
    exit 1
fi

NODE_COUNT=$(echo "$NODES" | wc -l)

# 如果展示全部，或 TOP_N 大于总数，则调整为全部
if [ "$SHOW_ALL" = true ] || [ "$TOP_N" -ge "$NODE_COUNT" ]; then
    TOP_N=$NODE_COUNT
    DISPLAY_HINT="全部 ${NODE_COUNT} 个"
else
    DISPLAY_HINT="Top ${TOP_N}（共 ${NODE_COUNT} 个）"
fi

echo -e "${GREEN}发现 ${NODE_COUNT} 个 Node，开始采集资源信息...${NC}"
echo -e "${CYAN}展示模式: ${DISPLAY_HINT} | 并发数: ${CONCURRENCY}${NC}"
echo ""

# 临时目录存储每个 Node 的采集结果（每个子进程写独立文件，避免写冲突）
TMP_DIR=$(mktemp -d /tmp/k8s-node-resources.XXXXXX)
TMP_FILE="${TMP_DIR}/_merged_result"
PROGRESS_DIR="${TMP_DIR}/_progress"
mkdir -p "$PROGRESS_DIR"

# ── 工具函数（定义在循环外部，供子进程使用） ─────────────────────────────────

# CPU 转为 millicores
convert_cpu_to_millicores() {
    local val="$1"
    if [[ "$val" == *m ]]; then
        echo "${val%m}"
    elif [[ "$val" =~ ^[0-9]+$ ]]; then
        echo $((val * 1000))
    else
        echo "0"
    fi
}

# 内存转为 MiB
convert_mem_to_mib() {
    local val="$1"
    if [[ "$val" == *Ki ]]; then
        echo $(( ${val%Ki} / 1024 ))
    elif [[ "$val" == *Mi ]]; then
        echo "${val%Mi}"
    elif [[ "$val" == *Gi ]]; then
        echo $(( ${val%Gi} * 1024 ))
    elif [[ "$val" == *Ti ]]; then
        echo $(( ${val%Ti} * 1024 * 1024 ))
    elif [[ "$val" =~ ^[0-9]+$ ]]; then
        # 纯数字视为字节
        echo $(( val / 1024 / 1024 ))
    else
        echo "0"
    fi
}

# 导出函数和变量供子进程使用
export -f convert_cpu_to_millicores convert_mem_to_mib

# 单个 Node 采集函数
collect_node() {
    local NODE="$1"
    local OUTPUT_FILE="$2"
    local PROGRESS_DIR="$3"

    # --- 获取 Allocatable 资源（可分配总量）---
    ALLOC_CPU_RAW=$(kubectl get node "$NODE" -o jsonpath='{.status.allocatable.cpu}' 2>/dev/null || echo "0")
    ALLOC_MEM_RAW=$(kubectl get node "$NODE" -o jsonpath='{.status.allocatable.memory}' 2>/dev/null || echo "0")

    # --- 获取 Capacity 资源（物理总量）---
    CAP_CPU_RAW=$(kubectl get node "$NODE" -o jsonpath='{.status.capacity.cpu}' 2>/dev/null || echo "0")
    CAP_MEM_RAW=$(kubectl get node "$NODE" -o jsonpath='{.status.capacity.memory}' 2>/dev/null || echo "0")

    ALLOC_CPU=$(convert_cpu_to_millicores "$ALLOC_CPU_RAW")
    ALLOC_MEM=$(convert_mem_to_mib "$ALLOC_MEM_RAW")
    CAP_CPU=$(convert_cpu_to_millicores "$CAP_CPU_RAW")
    CAP_MEM=$(convert_mem_to_mib "$CAP_MEM_RAW")

    # --- 从 describe node 中提取 Allocated resources 的 requests ---
    DESCRIBE=$(kubectl describe node "$NODE" 2>/dev/null)

    REQ_CPU_RAW=$(echo "$DESCRIBE" | awk '/Allocated resources/,/Events/' | grep -E '^\s+cpu' | head -1 | awk '{print $2}' || echo "0")
    REQ_MEM_RAW=$(echo "$DESCRIBE" | awk '/Allocated resources/,/Events/' | grep -E '^\s+memory' | head -1 | awk '{print $2}' || echo "0")

    REQ_CPU=$(convert_cpu_to_millicores "${REQ_CPU_RAW:-0}")
    REQ_MEM=$(convert_mem_to_mib "${REQ_MEM_RAW:-0}")

    # --- 计算剩余 ---
    REMAIN_CPU=$((ALLOC_CPU - REQ_CPU))
    REMAIN_MEM=$((ALLOC_MEM - REQ_MEM))

    [ "$REMAIN_CPU" -lt 0 ] && REMAIN_CPU=0
    [ "$REMAIN_MEM" -lt 0 ] && REMAIN_MEM=0

    # 计算使用百分比
    if [ "$ALLOC_CPU" -gt 0 ]; then
        USED_CPU_PCT=$((REQ_CPU * 100 / ALLOC_CPU))
    else
        USED_CPU_PCT=0
    fi
    if [ "$ALLOC_MEM" -gt 0 ]; then
        USED_MEM_PCT=$((REQ_MEM * 100 / ALLOC_MEM))
    else
        USED_MEM_PCT=0
    fi

    # 获取 Node 状态
    NODE_STATUS=$(kubectl get node "$NODE" --no-headers -o custom-columns=STATUS:.status.conditions[-1].type 2>/dev/null || echo "Unknown")

    # 输出到独立文件
    echo "${REMAIN_CPU}|${REMAIN_MEM}|${NODE}|${CAP_CPU}|${ALLOC_CPU}|${REQ_CPU}|${REMAIN_CPU}|${USED_CPU_PCT}|${CAP_MEM}|${ALLOC_MEM}|${REQ_MEM}|${REMAIN_MEM}|${USED_MEM_PCT}|${NODE_STATUS}" > "$OUTPUT_FILE"

    # 写入进度标记
    touch "${PROGRESS_DIR}/${NODE}"
}
export -f collect_node

# ── Step 2: 并发采集每个 Node 的资源使用情况 ──────────────────────────────────

# 使用 FIFO 管道作为信号量控制并发数
FIFO="${TMP_DIR}/_fifo"
mkfifo "$FIFO"
exec 6<>"$FIFO"  # 文件描述符 6 关联到 FIFO

# 预填充令牌（并发数个）
for ((i = 0; i < CONCURRENCY; i++)); do
    echo >&6
done

# 启动并发采集
for NODE in $NODES; do
    # 读取一个令牌（阻塞直到有空闲槽位）
    read -u 6

    (
        collect_node "$NODE" "${TMP_DIR}/${NODE}.dat" "$PROGRESS_DIR"
        # 归还令牌
        echo >&6
    ) &
done

# 进度显示（后台轮询）
(
    while true; do
        DONE_COUNT=$(ls "$PROGRESS_DIR" 2>/dev/null | wc -l)
        echo -ne "\r  采集中... [${DONE_COUNT}/${NODE_COUNT}]          "
        if [ "$DONE_COUNT" -ge "$NODE_COUNT" ]; then
            break
        fi
        sleep 0.3
    done
) &
PROGRESS_PID=$!

# 等待所有后台采集任务完成
wait 2>/dev/null

# 停止进度显示
kill "$PROGRESS_PID" 2>/dev/null
wait "$PROGRESS_PID" 2>/dev/null

# 关闭 FIFO 文件描述符
exec 6>&-

echo -ne "\r  采集完成! [${NODE_COUNT}/${NODE_COUNT}]                    \n"
echo ""

# 合并所有子进程的结果到一个文件
cat "${TMP_DIR}"/*.dat > "$TMP_FILE" 2>/dev/null

# 设置清理 trap
trap "rm -rf $TMP_DIR" EXIT

# ── Step 3: 排序并输出 ───────────────────────────────────────────────────────

# 格式化 CPU 显示（millicores → cores 或 millicores）
fmt_cpu() {
    local mc="$1"
    if [ "$mc" -ge 1000 ]; then
        local cores=$((mc / 1000))
        local remainder=$((mc % 1000))
        if [ "$remainder" -eq 0 ]; then
            echo "${cores}c"
        else
            echo "${cores}.$(printf '%03d' $remainder | sed 's/0*$//')c"
        fi
    else
        echo "${mc}m"
    fi
}

# 格式化 内存 显示（MiB → GiB 或 MiB）
fmt_mem() {
    local mib="$1"
    if [ "$mib" -ge 1024 ]; then
        local gib=$((mib / 1024))
        local remainder=$((mib % 1024))
        if [ "$remainder" -eq 0 ]; then
            echo "${gib}Gi"
        else
            # 保留1位小数
            local decimal=$(( remainder * 10 / 1024 ))
            echo "${gib}.${decimal}Gi"
        fi
    else
        echo "${mib}Mi"
    fi
}

# 颜色判断函数（根据使用率）
color_pct() {
    local pct="$1"
    if [ "$pct" -ge 80 ]; then
        echo -e "${RED}${pct}%${NC}"
    elif [ "$pct" -ge 60 ]; then
        echo -e "${YELLOW}${pct}%${NC}"
    else
        echo -e "${GREEN}${pct}%${NC}"
    fi
}

# ── 生成排序文件（供资源表格和污点表格共用）──
SORTED_FILE="${TMP_DIR}/_sorted_result"
sort -t'|' -k1 -rn "$TMP_FILE" | head -n "$TOP_N" > "$SORTED_FILE"

# ── 按 CPU 剩余量排序（从高到低）──
echo -e "${BOLD}┌──────────────────────────────────────────────────────────────────────────────────────────────────────────┐${NC}"
printf  "${BOLD}│ %-104s │${NC}\n" "Node 资源分析（按 CPU 剩余量从高到低 · ${DISPLAY_HINT}）"
echo -e "${BOLD}├────────────────────────────────────┬────────┬──────────────────────────────┬──────────────────────────────┤${NC}"
printf  "${BOLD}│ %-34s │ %-6s │ %-37s │ %-37s │${NC}\n" "Node Name" "Status" "CPU (总量/已分配/剩余/占比)" "内存 (总量/已分配/剩余/占比)"
echo -e "${BOLD}├────────────────────────────────────┼────────┼──────────────────────────────┼──────────────────────────────┤${NC}"

# 输出 Top N 行
while IFS='|' read -r _sort_cpu _sort_mem NODE CAP_CPU ALLOC_CPU REQ_CPU REMAIN_CPU USED_CPU_PCT CAP_MEM ALLOC_MEM REQ_MEM REMAIN_MEM USED_MEM_PCT STATUS; do

    # 格式化显示
    CPU_INFO="$(fmt_cpu "$CAP_CPU")/$(fmt_cpu "$REQ_CPU")/$(fmt_cpu "$REMAIN_CPU")"
    MEM_INFO="$(fmt_mem "$CAP_MEM")/$(fmt_mem "$REQ_MEM")/$(fmt_mem "$REMAIN_MEM")"

    CPU_PCT_COLOR=$(color_pct "$USED_CPU_PCT")
    MEM_PCT_COLOR=$(color_pct "$USED_MEM_PCT")

    # 截断过长的 Node 名
    NODE_SHORT="${NODE:0:34}"

    printf "│ %-34s │ %-6s │ %-28s %b │ %-28s %b │\n" \
        "$NODE_SHORT" "$STATUS" "$CPU_INFO" "$CPU_PCT_COLOR" "$MEM_INFO" "$MEM_PCT_COLOR"
done < "$SORTED_FILE"

echo -e "${BOLD}└────────────────────────────────────┴────────┴──────────────────────────────┴──────────────────────────────┘${NC}"

# ── 汇总 ──
echo ""
TOTAL_ALLOC_CPU=0
TOTAL_REQ_CPU=0
TOTAL_REMAIN_CPU=0
TOTAL_ALLOC_MEM=0
TOTAL_REQ_MEM=0
TOTAL_REMAIN_MEM=0

while IFS='|' read -r _sort_cpu _sort_mem NODE CAP_CPU ALLOC_CPU REQ_CPU REMAIN_CPU USED_CPU_PCT CAP_MEM ALLOC_MEM REQ_MEM REMAIN_MEM USED_MEM_PCT STATUS; do
    TOTAL_ALLOC_CPU=$((TOTAL_ALLOC_CPU + ALLOC_CPU))
    TOTAL_REQ_CPU=$((TOTAL_REQ_CPU + REQ_CPU))
    TOTAL_REMAIN_CPU=$((TOTAL_REMAIN_CPU + REMAIN_CPU))
    TOTAL_ALLOC_MEM=$((TOTAL_ALLOC_MEM + ALLOC_MEM))
    TOTAL_REQ_MEM=$((TOTAL_REQ_MEM + REQ_MEM))
    TOTAL_REMAIN_MEM=$((TOTAL_REMAIN_MEM + REMAIN_MEM))
done < "$TMP_FILE"

if [ "$TOTAL_ALLOC_CPU" -gt 0 ]; then
    TOTAL_CPU_PCT=$((TOTAL_REQ_CPU * 100 / TOTAL_ALLOC_CPU))
else
    TOTAL_CPU_PCT=0
fi
if [ "$TOTAL_ALLOC_MEM" -gt 0 ]; then
    TOTAL_MEM_PCT=$((TOTAL_REQ_MEM * 100 / TOTAL_ALLOC_MEM))
else
    TOTAL_MEM_PCT=0
fi

echo -e "${CYAN}${BOLD}=== 集群汇总 ===${NC}"
echo -e "  节点总数:    ${BOLD}${NODE_COUNT}${NC}"
echo -e "  CPU  可分配: $(fmt_cpu $TOTAL_ALLOC_CPU)   已分配: $(fmt_cpu $TOTAL_REQ_CPU)   剩余: ${GREEN}$(fmt_cpu $TOTAL_REMAIN_CPU)${NC}   使用率: $(color_pct $TOTAL_CPU_PCT)"
echo -e "  内存 可分配: $(fmt_mem $TOTAL_ALLOC_MEM)   已分配: $(fmt_mem $TOTAL_REQ_MEM)   剩余: ${GREEN}$(fmt_mem $TOTAL_REMAIN_MEM)${NC}   使用率: $(color_pct $TOTAL_MEM_PCT)"
echo ""
echo -e "${YELLOW}注: 已分配 = Pod Requests 总量（非实际使用量），剩余 = 可分配 - 已分配${NC}"
echo ""

# ── Step 4: 展示 Top N Node 的污点信息 ───────────────────────────────────────
echo -e "${CYAN}${BOLD}=== Top ${TOP_N} Node 污点 (Taints) 信息 ===${NC}"
echo ""
echo -e "${BOLD}┌────────────────────────────────────┬──────────────────────────────────────┬────────────────────┐${NC}"
printf  "${BOLD}│ %-34s │ %-36s │ %-18s │${NC}\n" "Node Name" "Taint Key=Value" "Effect"
echo -e "${BOLD}├────────────────────────────────────┼──────────────────────────────────────┼────────────────────┤${NC}"

while IFS='|' read -r _sort_cpu _sort_mem NODE _rest; do
    # 获取节点污点信息（JSON 数组）
    TAINTS_JSON=$(kubectl get node "$NODE" -o jsonpath='{.spec.taints}' 2>/dev/null || echo "")
    NODE_SHORT="${NODE:0:34}"

    if [ -z "$TAINTS_JSON" ] || [ "$TAINTS_JSON" = "null" ]; then
        # 无污点
        printf "│ %-34s │ %-36s │ %-18s │\n" "$NODE_SHORT" "(无污点)" "-"
    else
        # 解析每个 taint：key=value:effect
        TAINT_COUNT=$(echo "$TAINTS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

        if [ "$TAINT_COUNT" -eq 0 ]; then
            printf "│ %-34s │ %-36s │ %-18s │\n" "$NODE_SHORT" "(无污点)" "-"
        else
            FIRST=true
            for i in $(seq 0 $((TAINT_COUNT - 1))); do
                TAINT_KEY=$(echo "$TAINTS_JSON" | python3 -c "import sys,json; t=json.load(sys.stdin)[$i]; print(t.get('key',''))" 2>/dev/null)
                TAINT_VALUE=$(echo "$TAINTS_JSON" | python3 -c "import sys,json; t=json.load(sys.stdin)[$i]; print(t.get('value',''))" 2>/dev/null)
                TAINT_EFFECT=$(echo "$TAINTS_JSON" | python3 -c "import sys,json; t=json.load(sys.stdin)[$i]; print(t.get('effect',''))" 2>/dev/null)

                if [ -n "$TAINT_VALUE" ]; then
                    TAINT_KV="${TAINT_KEY}=${TAINT_VALUE}"
                else
                    TAINT_KV="${TAINT_KEY}"
                fi

                # 截断过长的 key=value
                TAINT_KV="${TAINT_KV:0:36}"
                TAINT_EFFECT="${TAINT_EFFECT:0:18}"

                # 为同一 node 的多个 taint，只在第一行显示 node 名
                if [ "$FIRST" = true ]; then
                    printf "│ %-34s │ %-36s │ %-18s │\n" "$NODE_SHORT" "$TAINT_KV" "$TAINT_EFFECT"
                    FIRST=false
                else
                    printf "│ %-34s │ %-36s │ %-18s │\n" "" "$TAINT_KV" "$TAINT_EFFECT"
                fi
            done
        fi
    fi
    # 每个 node 之间加分隔线（轻量级）
    echo -e "├────────────────────────────────────┼──────────────────────────────────────┼────────────────────┤"
done < "$SORTED_FILE"

# 替换最后一行分隔线为底部边框
echo -ne "\033[1A"  # 光标上移一行
echo -e "${BOLD}└────────────────────────────────────┴──────────────────────────────────────┴────────────────────┘${NC}"
echo ""
