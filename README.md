# simple-mcp

MCP（Model Context Protocol）に準拠したシンプルな HTTP ベースの MCP Server です。
Python 標準ライブラリのみで動作し、ツール・プロンプト・リソースを提供します。

## 提供する機能

### Tools
| ツール名 | 説明 |
| --- | --- |
| `get_test_string` | テスト用文字列を返します（`prefix` オプション対応） |
| `echo` | 入力メッセージをそのまま返します |
| `check_maintenance` | `secret_notes.txt` のメンテナンス情報を返します |

### Prompts
| プロンプト名 | 説明 |
| --- | --- |
| `greeting` | 挨拶プロンプト（`name` 引数対応） |

### Resources
| URI | 説明 |
| --- | --- |
| `demo://test-data` | デモ用テストデータ |

### その他の機能
- **OAuth 2.1** Bearer トークン検証（JWKS / RS256）※オプション・デフォルト無効
- CORS 対応
- RFC 9728 Protected Resource Metadata エンドポイント（`/.well-known/oauth-protected-resource`）
- MCP プロトコルバージョン `2024-11-05`

## 必要環境
- Python 3.8 以上
- OAuth 認証を使う場合のみ追加パッケージが必要（`requirements.txt` 参照）

## セットアップ

```bash
# 仮想環境の作成
python3 -m venv venv
source venv/bin/activate

# 依存パッケージのインストール（OAuth を使わない場合は省略可）
pip install -r requirements.txt

# 設定ファイルの準備
cp mcp_server_config.json.example mcp_server_config.json

# メンテナンス情報ファイルの準備（自動生成されますが、手動で用意も可能）
mkdir -p mcp-server-data
cp mcp-server-data/secret_notes.txt.example mcp-server-data/secret_notes.txt
```

## 起動

```bash
# 設定ファイルのポートで起動（デフォルト: 9000）
python mcpServer.py

# ポートを指定して起動
python mcpServer.py 9001
```

起動後、`http://localhost:9000/` でリクエストを待ち受けます。

## 動作確認

```bash
# ヘルスチェック（GET）
curl http://localhost:9000/

# initialize リクエスト（POST）
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# ツール一覧の取得
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# ツールの実行（echo）
curl -X POST http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello"}}}'
```

## 設定（`mcp_server_config.json`）

| 項目 | 説明 | デフォルト |
| --- | --- | --- |
| `host` | 待ち受けホスト | `0.0.0.0` |
| `port` | 待ち受けポート | `9000` |
| `server_info` | サーバー名・バージョン | `simple-demo-server v1.0.0` |
| `secret_file_path` | メンテナンス情報ファイルのパス | `./mcp-server-data/secret_notes.txt` |
| `check_maintenance_description` | `check_maintenance` ツールの説明文 | （例を参照） |
| `check_maintenance_prefix` | 返却時のプレフィックス文字列 | `maintenance information` |
| `oauth` | OAuth 認証設定（`enabled` / `issuer` / `jwks_uri` / `audience` / `scopes`） | `enabled: false` |

## ディレクトリ構成

```
simple-mcp/
├── mcpServer.py                          # MCP Server 本体
├── mcp_server_config.json.example        # 設定ファイル例
├── mcp-server-data/
│   └── secret_notes.txt.example          # メンテナンス情報ファイル例
├── requirements.txt                      # OAuth 利用時の依存パッケージ
└── README.md
```
