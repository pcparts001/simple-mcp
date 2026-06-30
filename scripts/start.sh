#!/usr/bin/env bash
#
# MCP Server をバックグラウンドで起動する（nohup + &）
# - 標準出力/標準エラーは logs/server.log へ追記（tail -f で確認）
# - 出力バッファリングを無効化（python3 -u）し、tail -f でリアルタイム表示
# - PID を mcp-server.pid に保存し、二重起動を防止
#
# 使い方:
#   ./scripts/start.sh          # 設定ファイルのポート（デフォルト9000）で起動
#   ./scripts/start.sh 9001     # ポートを指定して起動
set -euo pipefail

# スクリプトの場所を基準にプロジェクトルートへ移動（どこから実行しても OK）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/mcp-server.pid"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/server.log"
SERVER_SCRIPT="$PROJECT_ROOT/mcpServer.py"

# 二重起動チェック: PIDファイルが存在し、かつプロセスが生存していれば終了
if [[ -f "$PID_FILE" ]]; then
    EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${EXISTING_PID:-}" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
        echo "⚠️  MCP Server は既に起動しています (PID: $EXISTING_PID)"
        echo "   停止してから再起動してください: ./scripts/stop.sh"
        echo "   状態確認: ./scripts/status.sh"
        exit 1
    fi
    # プロセスが無いのにPIDファイルが残っている場合は掃除
    rm -f "$PID_FILE"
fi

# ログディレクトリの準備
mkdir -p "$LOG_DIR"

# venv があれば有効化（OAuth 認証を使わなければ不要。標準ライブラリのみで動く）
if [[ -f "$PROJECT_ROOT/venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/venv/bin/activate"
    PYTHON_BIN="python"
    echo "✅ venv を有効化しました"
else
    PYTHON_BIN="python3"
    echo "ℹ️  venv が見つかりません。システムの python3 を使用します"
fi

# ポート（引数があればそれを使い、無ければ mcpServer.py のデフォルトに委譲）
PORT_ARG="${1:-}"

# 起動時の区切り線と時刻をログへ記録
{
    echo ""
    echo "============================================================"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] MCP Server starting"
    echo "  Port: ${PORT_ARG:-<config default>}"
    echo "============================================================"
} >> "$LOG_FILE"

# バックグラウンド起動（-u でバッファリング無効化 → tail -f で即時表示）
# nohup によりターミナル切断後も継続動作
nohup "$PYTHON_BIN" -u "$SERVER_SCRIPT" ${PORT_ARG:+$PORT_ARG} >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# 起動直後の生存確認（即死していたら検知）
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "❌ 起動に失敗しました。ログを確認してください: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi

echo "✅ MCP Server をバックグラウンドで起動しました"
echo "   PID:  $SERVER_PID"
echo "   Port: ${PORT_ARG:-<config default (9000)>}"
echo ""
echo "   ログ確認:    tail -f $LOG_FILE"
echo "   状態確認:    ./scripts/status.sh"
echo "   停止:        ./scripts/stop.sh"
