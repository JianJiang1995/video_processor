#!/bin/bash
# ============================================================
# 视频流会话完整清理脚本
# ============================================================
# 清理内容:
#   1. sessions文件夹 - 视频帧图片 (~336GB)
#   2. uploads文件夹 - 上传的视频文件
#   3. output文件夹 - 输出文件
#   4. MySQL数据库:
#      - video_sessions 表
#      - analysis_results 表
#      - chat_history 表
# ============================================================

# 配置路径
BASE_DIR="/data2/jj/proj/video_processor/video_stream_app"
SESSIONS_DIR="$BASE_DIR/sessions"
UPLOADS_DIR="$BASE_DIR/uploads"
OUTPUT_DIR="$BASE_DIR/output"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}     视频流会话完整清理脚本${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ============================================================
# 函数: 显示目录状态
# ============================================================
show_dir_status() {
    local dir="$1"
    local name="$2"
    
    if [ -d "$dir" ]; then
        local size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        local count=$(find "$dir" -type f 2>/dev/null | wc -l)
        echo -e "  ${name}: ${YELLOW}${size}${NC} (${count} 个文件)"
    else
        echo -e "  ${name}: ${GREEN}(目录不存在)${NC}"
    fi
}

# ============================================================
# 函数: 使用Python显示/清理数据库
# ============================================================
show_db_status() {
    python3 << 'PYEOF'
import sys
sys.path.insert(0, '/data2/jj/proj/video_processor/video_stream_app/backend')
try:
    import json
    with open('/data2/jj/proj/video_processor/video_stream_app/config.json') as f:
        config = json.load(f)
    mysql_cfg = config.get('database', {}).get('mysql', {})
    
    import pymysql
    conn = pymysql.connect(
        host=mysql_cfg.get('host', 'localhost'),
        port=mysql_cfg.get('port', 3306),
        user=mysql_cfg.get('user', 'root'),
        password=mysql_cfg.get('password', ''),
        database=mysql_cfg.get('database', 'video_analyzer')
    )
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM video_sessions")
    sessions = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM analysis_results")
    analysis = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM chat_history")
    chat = cursor.fetchone()[0]
    
    print(f"  video_sessions:   \033[1;33m{sessions}\033[0m 条记录")
    print(f"  analysis_results: \033[1;33m{analysis}\033[0m 条记录")
    print(f"  chat_history:     \033[1;33m{chat}\033[0m 条记录")
    
    conn.close()
except Exception as e:
    print(f"  \033[0;31m⚠ 无法连接MySQL: {e}\033[0m")
    sys.exit(1)
PYEOF
    return $?
}

clean_database() {
    python3 << 'PYEOF'
import sys
sys.path.insert(0, '/data2/jj/proj/video_processor/video_stream_app/backend')
try:
    import json
    with open('/data2/jj/proj/video_processor/video_stream_app/config.json') as f:
        config = json.load(f)
    mysql_cfg = config.get('database', {}).get('mysql', {})
    
    import pymysql
    conn = pymysql.connect(
        host=mysql_cfg.get('host', 'localhost'),
        port=mysql_cfg.get('port', 3306),
        user=mysql_cfg.get('user', 'root'),
        password=mysql_cfg.get('password', ''),
        database=mysql_cfg.get('database', 'video_analyzer')
    )
    cursor = conn.cursor()
    
    # TRUNCATE 比 DELETE 更快
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("TRUNCATE TABLE chat_history")
    cursor.execute("TRUNCATE TABLE analysis_results")
    cursor.execute("TRUNCATE TABLE video_sessions")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
    
    conn.commit()
    conn.close()
    print("OK")
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)
PYEOF
    return $?
}

# ============================================================
# 显示当前状态
# ============================================================
echo -e "📁 ${BLUE}文件存储状态:${NC}"
show_dir_status "$SESSIONS_DIR" "sessions (帧图片)"
show_dir_status "$UPLOADS_DIR" "uploads (上传视频)"
show_dir_status "$OUTPUT_DIR" "output (输出文件)"

echo -e "\n📊 ${BLUE}数据库状态:${NC}"
DB_OK=true
show_db_status || DB_OK=false

echo ""

# ============================================================
# 清理选项菜单
# ============================================================
echo -e "${BLUE}请选择清理选项:${NC}"
echo "  1) 仅清理 sessions 文件夹 (帧图片)"
echo "  2) 清理所有文件夹 (sessions + uploads + output)"
echo "  3) 仅清理数据库 (保留文件)"
echo "  4) 完整清理 (文件夹 + 数据库) ⭐ 推荐"
echo "  5) 退出"
echo ""
read -p "请输入选项 [1-5]: " choice

case $choice in
    1)
        CLEAN_SESSIONS=true
        CLEAN_UPLOADS=false
        CLEAN_OUTPUT=false
        CLEAN_DB=false
        ;;
    2)
        CLEAN_SESSIONS=true
        CLEAN_UPLOADS=true
        CLEAN_OUTPUT=true
        CLEAN_DB=false
        ;;
    3)
        CLEAN_SESSIONS=false
        CLEAN_UPLOADS=false
        CLEAN_OUTPUT=false
        CLEAN_DB=true
        ;;
    4)
        CLEAN_SESSIONS=true
        CLEAN_UPLOADS=true
        CLEAN_OUTPUT=true
        CLEAN_DB=true
        ;;
    5|*)
        echo -e "${YELLOW}已取消${NC}"
        exit 0
        ;;
esac

# ============================================================
# 确认删除
# ============================================================
echo ""
echo -e "${RED}⚠️  警告: 此操作不可恢复!${NC}"
echo "将要执行的操作:"
$CLEAN_SESSIONS && echo "  - 删除 sessions 文件夹内容"
$CLEAN_UPLOADS && echo "  - 删除 uploads 文件夹内容"
$CLEAN_OUTPUT && echo "  - 删除 output 文件夹内容"
$CLEAN_DB && echo "  - 清空数据库表 (video_sessions, analysis_results, chat_history)"
echo ""
read -p "确认执行? (输入 'yes' 确认): " confirm

if [ "$confirm" != "yes" ]; then
    echo -e "${YELLOW}已取消${NC}"
    exit 0
fi

echo ""
echo -e "${BLUE}🗑️  开始清理...${NC}"

# ============================================================
# 执行清理
# ============================================================

# 清理 sessions
if $CLEAN_SESSIONS && [ -d "$SESSIONS_DIR" ]; then
    echo -n "  清理 sessions..."
    rm -rf "$SESSIONS_DIR"/*
    echo -e " ${GREEN}✓${NC}"
fi

# 清理 uploads
if $CLEAN_UPLOADS && [ -d "$UPLOADS_DIR" ]; then
    echo -n "  清理 uploads..."
    rm -rf "$UPLOADS_DIR"/*
    echo -e " ${GREEN}✓${NC}"
fi

# 清理 output
if $CLEAN_OUTPUT && [ -d "$OUTPUT_DIR" ]; then
    echo -n "  清理 output..."
    rm -rf "$OUTPUT_DIR"/*
    echo -e " ${GREEN}✓${NC}"
fi

# 清理数据库
if $CLEAN_DB && $DB_OK; then
    echo -n "  清理数据库..."
    result=$(clean_database)
    if [ "$result" = "OK" ]; then
        echo -e " ${GREEN}✓${NC}"
    else
        echo -e " ${RED}$result${NC}"
    fi
fi

# ============================================================
# 显示清理后状态
# ============================================================
echo ""
echo -e "${GREEN}✅ 清理完成!${NC}"
echo ""
echo -e "📁 ${BLUE}清理后状态:${NC}"
show_dir_status "$SESSIONS_DIR" "sessions"
show_dir_status "$UPLOADS_DIR" "uploads"
show_dir_status "$OUTPUT_DIR" "output"

if $DB_OK; then
    echo -e "\n📊 ${BLUE}数据库状态:${NC}"
    show_db_status
fi

echo ""
