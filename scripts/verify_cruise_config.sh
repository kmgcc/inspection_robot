#!/usr/bin/env bash
# 验证连续巡航测试配置是否正确

set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"

echo "=========================================="
echo "连续巡航配置验证工具"
echo "=========================================="
echo ""

# 1. 检查本地启动脚本配置
echo "✓ 检查本地启动脚本 (scripts/run_on_car.sh):"
if grep -q 'LINE_FOLLOW_ENABLED="${LINE_FOLLOW_ENABLED:-0}"' scripts/run_on_car.sh; then
    echo "  ✅ LINE_FOLLOW_ENABLED 默认值为 0（循线已禁用）"
else
    echo "  ⚠️  LINE_FOLLOW_ENABLED 配置异常"
    grep "LINE_FOLLOW_ENABLED" scripts/run_on_car.sh || true
fi

if grep -q 'AUTO_START_RUNTIME' scripts/run_on_car.sh app.py scripts/install_car_autostart.sh; then
    echo "  ⚠️  检测到 AUTO_START_RUNTIME 残留配置"
    grep -n "AUTO_START_RUNTIME" scripts/run_on_car.sh app.py scripts/install_car_autostart.sh || true
else
    echo "  ✅ 已移除 AUTO_START_RUNTIME 自启开关，巡逻只能手动启动"
fi

if grep -q 'SMOOTH_CRUISE_ENABLED="${SMOOTH_CRUISE_ENABLED:-1}"' scripts/run_on_car.sh; then
    echo "  ✅ SMOOTH_CRUISE_ENABLED 默认值为 1（匀速巡航启用）"
else
    echo "  ⚠️  SMOOTH_CRUISE_ENABLED 配置异常"
fi
echo ""

# 2. 检查小车端运行状态（如果可访问）
echo "✓ 检查小车端运行状态:"
if ssh -o ConnectTimeout=3 "$CAR_HOST" "test -d '$CAR_DIR'" 2>/dev/null; then
    echo "  ✅ 小车可访问 ($CAR_HOST)"

    # 检查是否有运行中的进程
    if ssh "$CAR_HOST" "cd '$CAR_DIR' && pgrep -f 'app.py' > /dev/null" 2>/dev/null; then
        echo "  ⚠️  检测到运行中的 app.py 进程"
        echo "     PID: $(ssh "$CAR_HOST" "pgrep -f 'app.py'" 2>/dev/null || echo 'unknown')"
        echo ""
        echo "  📋 运行环境变量检查:"
        ssh "$CAR_HOST" "cd '$CAR_DIR' && ps aux | grep '[a]pp.py' | head -1" 2>/dev/null || true
    else
        echo "  ℹ️  未检测到运行中的进程"
    fi

    # 检查日志中的配置
    if ssh "$CAR_HOST" "test -f '$CAR_DIR/app.log'" 2>/dev/null; then
        echo ""
        echo "  📋 最近的配置记录 (app.log):"
        ssh "$CAR_HOST" "cd '$CAR_DIR' && tail -50 app.log | grep -E 'LINE_FOLLOW_ENABLED|SMOOTH_CRUISE_ENABLED|Started on' | tail -5" 2>/dev/null || echo "     未找到配置记录"
    fi
else
    echo "  ⚠️  无法连接到小车 ($CAR_HOST)"
    echo "     请检查网络连接或 CAR_HOST 配置"
fi
echo ""

# 3. 检查 API 状态（如果服务运行中）
echo "✓ 检查 API 服务状态:"
if curl -s --connect-timeout 3 "http://${CAR_HOST#*@}:5000/health" > /dev/null 2>&1; then
    echo "  ✅ API 服务运行中"

    # 获取当前状态
    STATUS=$(curl -s "http://${CAR_HOST#*@}:5000/api/status" 2>/dev/null || echo '{}')
    TASK_STATUS=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('task_status', 'UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    RUN_MODE=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('run_mode', 'unknown'))" 2>/dev/null || echo "unknown")

    echo "  📊 当前状态:"
    echo "     task_status: $TASK_STATUS"
    echo "     run_mode: $RUN_MODE"

    if [ "$TASK_STATUS" = "PATROLLING" ]; then
        echo "  ⚠️  小车正在巡逻中！"
        echo "     如需停止: curl -X POST http://${CAR_HOST#*@}:5000/api/stop"
    fi
else
    echo "  ℹ️  API 服务未运行"
    echo "     启动方法: ./scripts/run_on_car.sh"
fi
echo ""

# 4. 配置建议
echo "=========================================="
echo "✅ 配置检查完成"
echo "=========================================="
echo ""
echo "📌 测试前确认清单:"
echo "   1. LINE_FOLLOW_ENABLED=0 (循线禁用)"
echo "   2. 开机后只启动网页服务，巡逻不能自动启动"
echo "   3. SMOOTH_CRUISE_ENABLED=1 (匀速巡航)"
echo "   4. 网页手动点击'开始巡逻'按钮启动"
echo ""
echo "📖 详细测试步骤请参考: CRUISE_TEST_CONFIG.md"
echo ""
