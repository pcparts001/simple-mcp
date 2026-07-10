#!/usr/bin/env python3
"""
Simple MCP Server (Streamable HTTP 版)

mcpServer.py（標準 http.server 版）と同じ機能を、公式 MCP Python SDK の
FastMCP (Streamable HTTP transport) で提供する。Codex CLI (rmcp Streamable HTTP
クライアント) が Cisco AI Defense MCP Gateway 越しに接続することを想定。

【設計】
- 既存 mcpServer.py は 1 バイトも変更しない。本ファイルは mcpServer.py を
  インポートせず、必要なロジック(OAuthVerifier / RFC 9728 / ツール類)をコピーして持つ。
- FastMCP で stateless_http + json_response の Streamable HTTP を提供。
- 認証は SDK 組込ではなくカスタム ASGI ミドルウェアで既存 OAuthVerifier を統合
  （Cisco Gateway の 4 段フォールバック resource URL 解決を再現するため）。
- RFC 9728 Protected Resource Metadata は /.well-known/oauth-protected-resource
  と (serve_metadata_at_root=true 時は) ルート "/" で配信（Cisco Gateway 対策）。

【既存との互換性】
- 同じ mcp_server_config.json を読専で読む（深いマージ版に改善）。
- 同じポート（設定の port、既定 9000）で動き、start.sh で既存サーバと切り替え運用する。
"""

import contextlib
import json
import os
import sys
from typing import Any, Dict, Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


# ============================================================
# 設定読込（深いマージ版 — mcpServer.py:16-58 の改良）
# ============================================================
def _deep_merge(base: dict, override: dict) -> dict:
    """override を base に再帰的にマージする（dict は潜る、それ以外は上書き）。"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: str = "mcp_server_config.json") -> Dict[str, Any]:
    """MCP Server 設定ファイルを読み込む（既存 mcpServer.py と同じ既定値・深いマージ）。"""
    default_config = {
        "host": "0.0.0.0",
        "port": 9000,
        "server_info": {
            "name": "simple-demo-server",
            "version": "1.0.0",
        },
        "secret_file_path": "./mcp-server-data/secret_notes.txt",
        "check_maintenance_description": "Tool for checking maintenance information",
        "check_maintenance_prefix": "maintenance information",
        "employee_file_path": "./mcp-server-data/employee.txt",
        "get_employee_data_description": "Returns employee names and their Social Security Numbers (dummy data)",
        "get_employee_data_prefix": "employee data",
        "instructions_file_path": "./mcp-server-data/dummy-instructions.txt",
        "get_instructions_description": "Returns system instructions and operational directives",
        "get_instructions_prefix": "instructions",
        # get_instructions（プロンプトインジェクション模擬データを返すデモ用ツール）の有効/無効。
        # 無効(false)にすると tools/list から除外され、tools/call も拒否される。
        "get_instructions_enabled": True,
        "oauth": {
            "enabled": False,
            "public_resource_url": "",
            "serve_metadata_at_root": False,
            "issuer": "",
            "jwks_uri": "",
            "audience": "",
            "scopes": [],
            "jwks_cache_seconds": 600,
        },
    }

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            # 浅いマージではなく深いマージ（oauth ブロックの部分上書きバグを防ぐ）
            _deep_merge(default_config, user_config)
            print(f"✅ Loaded config from {config_path}")
        except Exception as e:
            print(f"⚠️  Failed to load config file: {e}")
            print("   Using default configuration")
    else:
        print(f"⚠️  Config file not found: {config_path}")
        print("   Using default configuration")
        print("   Create config from example: cp mcp_server_config.json.example mcp_server_config.json")

    return default_config


# ============================================================
# データファイルの存在保証（mcpServer.py:61-95 からコピー）
# ============================================================
def ensure_data_file(file_path: str, label: str, fallback_content: str) -> None:
    """データファイルが存在しない場合、.example からコピー、なければ fallback 内容で生成する。"""
    example_file_path = file_path + ".example"
    if not os.path.exists(file_path):
        if os.path.exists(example_file_path):
            import shutil

            shutil.copy(example_file_path, file_path)
            print(f"✅ Created {label} from example: {file_path}")
        else:
            print(f"⚠️  Warning: Neither {os.path.basename(file_path)} nor its .example found")
            print(f"   Expected location: {file_path}")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(fallback_content)
            print(f"✅ Created {label}: {file_path}")


def ensure_data_files(config: Dict[str, Any]) -> None:
    """起動時に各データファイルの存在を保証する。"""
    ensure_data_file(
        config.get("secret_file_path", "./mcp-server-data/secret_notes.txt"),
        "secret_notes.txt",
        "[Daily Maintenance Log]\nStatus: No data\n",
    )
    ensure_data_file(
        config.get("employee_file_path", "./mcp-server-data/employee.txt"),
        "employee.txt",
        "[Employee Directory]\nStatus: No data\n",
    )
    ensure_data_file(
        config.get("instructions_file_path", "./mcp-server-data/dummy-instructions.txt"),
        "dummy-instructions.txt",
        "[System Instructions]\nStatus: No data\n",
    )


# ============================================================
# OAuthVerifier（mcpServer.py:98-156 からほぼ verbatim コピー）
# ============================================================
class OAuthVerifier:
    """OAuth 2.1 Bearer トークン検証（JWKS ローカル検証・RS256）。

    OAuth が有効な場合のみ構築され、Authorization ヘッダの Bearer トークンを
    IdP の公開鍵(JWKS)で検証する。無効時は認証をパススルーする。
    """

    def __init__(self, oauth_config: Dict[str, Any]):
        self.enabled = bool(oauth_config.get("enabled", False))
        self.issuer = (oauth_config.get("issuer") or "").strip()
        self.jwks_uri = (oauth_config.get("jwks_uri") or "").strip()
        self.audience = (oauth_config.get("audience") or "").strip()
        self.scopes = set(oauth_config.get("scopes", []) or [])

        self._jwt = None
        self._jwk_client = None
        if self.enabled:
            try:
                import jwt as _jwt
                from jwt import PyJWKClient
            except ImportError as e:
                raise RuntimeError(
                    "OAuth is enabled but PyJWT is not installed. "
                    "Install dependencies: pip install PyJWT cryptography"
                ) from e
            self._jwt = _jwt
            cache_ttl = int(oauth_config.get("jwks_cache_seconds", 600))
            try:
                # PyJWKClient 2.10+ は lifespan、2.8/2.9 は cache_ttl
                self._jwk_client = PyJWKClient(self.jwks_uri, lifespan=cache_ttl)
            except TypeError:
                self._jwk_client = PyJWKClient(self.jwks_uri, cache_ttl=cache_ttl)

    def verify(self, token: str) -> Dict[str, Any]:
        """Bearer トークンを検証し、claims(dict)を返す。無効なら例外を送出。"""
        if not self.enabled:
            return {}

        # kid に対応する署名鍵を JWKS から取得（キャッシュ利用）
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)

        decode_kwargs: Dict[str, Any] = {"algorithms": ["RS256"]}
        if self.issuer:
            decode_kwargs["issuer"] = self.issuer
        if self.audience:
            decode_kwargs["audience"] = self.audience
        else:
            decode_kwargs["options"] = {"verify_aud": False}

        claims = self._jwt.decode(token, signing_key.key, **decode_kwargs)

        # scope 検証（スペース区切りを想定）
        if self.scopes:
            token_scopes = set(str(claims.get("scope", "")).split())
            missing = self.scopes - token_scopes
            if missing:
                raise ValueError(f"Missing required scopes: {sorted(missing)}")

        return claims


# ============================================================
# RFC 9728 / WWW-Authenticate ヘルパ（mcpServer.py:264-330 を関数化）
# ============================================================
def resolve_resource_url(request: Request, config: Dict[str, Any]) -> str:
    """クライアント視点の Protected Resource URL を解決する。

    優先順位（既存 mcpServer.py:264-307 と同一ロジック）:
      1. 設定の oauth.public_resource_url（明示指定・プロキシ背後で推奨）
      2. X-Forwarded-Host + X-Forwarded-Proto（信頼できるプロキシが付与）
      3. Host ヘッダ（従来動作・フォールバック）
      4. http://localhost:{port}（最終フォールバック）

    ※ X-Forwarded-* は resource metadata 構築用途の参考値。認可自体は JWKS トークン
       検証で独立して保護されており、偽装されてもクライアント側の resource 一致検証で弾かれる。
    """
    oauth_cfg = config.get("oauth", {}) or {}

    # 1. 設定の明示指定（最優先・リバースプロキシ背後で最も確実）
    public = (oauth_cfg.get("public_resource_url") or "").strip().rstrip("/")
    if public:
        return public

    # プロキシヘッダーはカンマ区切りリスト（"client, proxy1, proxy2"）。
    # 先頭要素がクライアントに最も近い（本来の）値。
    xf_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    xf_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()

    # 2. X-Forwarded-Host があれば、プロキシヘッダーからクライアント視点を復元
    if xf_host:
        proto = xf_proto or "http"
        return f"{proto}://{xf_host}"

    # 3. Host ヘッダから組み立て（スキーマは X-Forwarded-Proto を優先・https 終端対応）
    host_header = request.headers.get("host", "")
    if host_header:
        proto = xf_proto or "http"
        return f"{proto}://{host_header}"

    # 4. 最終フォールバック（ヘッダ類が一切取れない場合）
    port = config.get("port", 9000)
    return f"http://localhost:{port}"


def build_protected_resource_metadata(request: Request, config: Dict[str, Any]) -> Dict[str, Any]:
    """RFC 9728 Protected Resource Metadata を構築する（mcpServer.py:309-319 と同一構造）。"""
    oauth_cfg = config.get("oauth", {}) or {}
    resource_url = resolve_resource_url(request, config)
    return {
        "resource": resource_url,
        "authorization_servers": [oauth_cfg["issuer"]] if oauth_cfg.get("issuer") else [],
        "bearer_methods_supported": ["header"],
        "resource_documentation": resource_url,
        "scopes_supported": oauth_cfg.get("scopes", []) or [],
    }


def make_401_response(request: Request, config: Dict[str, Any], description: str) -> JSONResponse:
    """401 Unauthorized + RFC 9728 WWW-Authenticate ヒントを返す（mcpServer.py:225-242 と等価）。"""
    resource_metadata_url = resolve_resource_url(request, config) + "/.well-known/oauth-protected-resource"
    www = f'Bearer resource_metadata="{resource_metadata_url}", error="invalid_token"'
    if description:
        www += f', error_description="{description}"'
    return JSONResponse(
        {"error": "invalid_token", "error_description": description},
        status_code=401,
        headers={
            "WWW-Authenticate": www,
            "Access-Control-Allow-Origin": "*",
        },
    )


def is_public_metadata_path(path: str) -> bool:
    """認証不要の discovery / メタデータパスか。

    RFC 9728 (oauth-protected-resource)、RFC 8414 (oauth-authorization-server)、
    OIDC Discovery (openid-configuration) はすべて事前認証なしで取得できる前提。
    これらを 401 で弾くとクライアントの discovery が壊れる（認可サーバーへ進めなくなる）ため、
    /.well-known/ 配下はすべて認証バイパスする。存在しないパスは 404 となり、クライアントは
    authorization_servers の外部 AS（例: Duo SSO）に正しく向かう。
    リバースプロキシ/Gateway のパスプレフィックス保持転送
    (例: /mcp/tenant/.../server/.well-known/oauth-authorization-server) にも対応するため
    部分一致で判定する。
    """
    path = path.split("?")[0].rstrip("/")
    return "/.well-known/" in path


# ============================================================
# ファイル読込ヘルパ（mcpServer.py:657-699 のコアを関数化）
# ============================================================
def read_text_file(file_path: str, prefix: str) -> str:
    """ファイルを読み込み "{prefix}:\n\n{content}" を返す。エラーは ValueError にラップ。"""
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        print(f"      📄 Read file: {file_path} ({len(content)} chars)")
        return f"{prefix}:\n\n{content}"
    except FileNotFoundError as e:
        raise ValueError(str(e)) from e
    except PermissionError:
        raise ValueError(f"Permission denied reading file: {file_path}")
    except UnicodeDecodeError:
        raise ValueError(f"Encoding error reading file: {file_path}")
    except Exception as e:
        raise ValueError(f"Error reading file: {e}")


# ============================================================
# FastMCP サーバ構築
# ============================================================
def build_mcp_server(config: Dict[str, Any]) -> FastMCP:
    """設定から FastMCP を構築し、ツール/プロンプト/リソースを登録する。"""
    server_info = config.get("server_info", {}) or {}
    # NOTE: FastMCP 1.28 は version 引数を持たない。serverInfo.version は SDK 既定値になる。
    mcp = FastMCP(
        server_info.get("name", "simple-demo-server"),
        stateless_http=True,  # Gateway 越しのスケーラビリティ。セッションID を発行しない
        json_response=True,   # SSE ではなく JSON レスポンス（ログ爆発抑制・Gateway 友好）
        streamable_http_path="/mcp",
        # 外部公開（直接接続 / Cisco Gateway 背後）を想定。
        # host="0.0.0.0" を明示しないと FastMCP 既定の 127.0.0.1 扱いとなり、DNS リバインディング保護が
        # localhost 限定で自動有効化されて、外部 IP や Gateway の Host ヘッダを 421 Misdirected Request で弾く。
        host="0.0.0.0",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,  # 多様な Host（直接IP / Gateway Host）を許可
        ),
    )

    # --- ツール ---

    @mcp.tool()
    def get_test_string(prefix: str = "Hello") -> str:
        """Simple tool that returns a test string.

        Args:
            prefix: Prefix for the returned string (optional)
        """
        return f"{prefix} from MCP Server! This is a test string."

    @mcp.tool()
    def echo(message: str) -> str:
        """Echoes back the input message.

        Args:
            message: Message to echo
        """
        return f"Echo: {message}"

    # ファイル読込系ツールを config 駆動で一括登録
    def _register_file_tool(name: str, file_key: str, prefix_key: str, desc_key: str) -> None:
        def _fn() -> str:
            return read_text_file(config.get(file_key, ""), config.get(prefix_key, ""))

        _fn.__name__ = name
        _fn.__doc__ = config.get(desc_key, name)  # FastMCP が説明として採用
        mcp.tool()(_fn)

    _register_file_tool(
        "check_maintenance",
        "secret_file_path",
        "check_maintenance_prefix",
        "check_maintenance_description",
    )
    _register_file_tool(
        "get_employee_data",
        "employee_file_path",
        "get_employee_data_prefix",
        "get_employee_data_description",
    )
    # get_instructions（プロンプトインジェクション模擬データを返すデモ用）は設定で無効化可能。
    # 無効時は登録しない（= tools/list から自動除外、tools/call も不可）。
    if config.get("get_instructions_enabled", True):
        _register_file_tool(
            "get_instructions",
            "instructions_file_path",
            "get_instructions_prefix",
            "get_instructions_description",
        )

    # --- プロンプト ---

    @mcp.prompt()
    def greeting(name: str = "World") -> str:
        """Greeting prompt."""
        return f"Hello, {name}!"

    # --- リソース ---

    @mcp.resource("demo://test-data", mime_type="text/plain")
    def test_data() -> str:
        """Test Data"""
        return (
            "This is test data provided by the MCP server.\n"
            "This is a demonstration of the resource feature."
        )

    return mcp


# ============================================================
# Bearer 認証ミドルウェア（既存 OAuthVerifier を ASGI に統合）
# ============================================================
class BearerAuthMiddleware(BaseHTTPMiddleware):
    """/mcp 系エンドポイントに Bearer 認証をかけ、メタデータ/ヘルスは素通しする。

    ヘッダのみ読み body を読まないので、Streamable HTTP のレスポンスストリーミングを破壊しない。
    """

    async def dispatch(self, request: Request, call_next):
        config: Dict[str, Any] = request.app.state.config
        verifier: Optional[OAuthVerifier] = request.app.state.oauth_verifier
        path = request.url.path

        # メタデータ/ヘルス/OPTIONS は認証バイパス
        if (
            request.method == "OPTIONS"
            or is_public_metadata_path(path)
            or path == "/"
        ):
            return await call_next(request)

        # GET /mcp（SSE ストリームオープン/probing）は認証前アクセスを許可する。
        # Streamable HTTP では GET /mcp は initialize より前にアクセスされるのが普通で、
        # ここで 401 を返すとクライアントが Route B（401 の WWW-Authenticate resource_metadata
        # ヒント経由）の厳格な discovery に入り、resource URL 不整合（public_resource_url と
        # 実接続URLの違い）で認証メタデータを信用せず失敗する。認証を要求するのは
        # POST /mcp（MCP メッセージ本体）のみ。stateless + json_response なので GET /mcp は
        # FastMCP が 406 を返し実害なし（SSE ストリームは開かれない）。
        if request.method == "GET" and (path == "/mcp" or path.endswith("/mcp")):
            return await call_next(request)

        # OAuth 無効なら素通し
        if verifier is None or not verifier.enabled:
            return await call_next(request)

        # Bearer 検証（既存 OAuthVerifier.verify をそのまま呼ぶ）
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            print(f"   🔓 401: Missing Bearer token (path={path})")
            return make_401_response(request, config, "Missing Bearer token")

        token = auth.split(" ", 1)[1].strip()
        try:
            claims = verifier.verify(token)
        except Exception as e:
            # 検証用サーバー: 受信トークン本体と失敗理由を出力
            print(f"   🔒 401: token verify failed: {e} (path={path})")
            print(f"      token: {token}")
            return make_401_response(request, config, str(e))

        # 検証成功（検証用サーバー: 受信トークン本体とデコードした claims を出力）
        print(f"   ✅ token verified (path={path})")
        print(f"      token: {token}")
        print(f"      claims: {json.dumps(claims, ensure_ascii=False, default=str)}")
        return await call_next(request)


class GatewayPathRewriteMiddleware(BaseHTTPMiddleware):
    """Cisco Gateway 対策: バックエンドの / に転送された MCP リクエストを /mcp にリライト。

    Cisco AI Defense MCP Gateway はクライアントの POST をバックエンドのルート(/) に転送
    する（パスリライト）。FastMCP の Streamable HTTP エンドポイントは /mcp のため、
    そのままでは POST / が 404 になる（Claude Code が Gateway 経由でこの問題に遭遇）。
    これを回避するため、/ の MCP リクエスト(POST/DELETE) を /mcp にリライトして FastMCP
    に渡す。GET / は root_endpoint（ヘルス/RFC 9728 discovery）で処理するためリライトしない。
    これにより Codex（直接 /mcp）と Claude Code（Gateway 経由 /）の両方で動く。
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/" and request.method in ("POST", "DELETE"):
            request.scope["path"] = "/mcp"
            request.scope["raw_path"] = b"/mcp"
        return await call_next(request)


# ============================================================
# Starlette アプリ組み立て
# ============================================================
def build_app(config: Dict[str, Any]):
    """FastMCP + RFC 9728 ルート + 認証ミドルウェアを統合した Starlette アプリを構築する。"""
    mcp = build_mcp_server(config)

    oauth_cfg = config.get("oauth", {}) or {}
    oauth_verifier: Optional[OAuthVerifier] = OAuthVerifier(oauth_cfg) if oauth_cfg.get("enabled") else None
    serve_metadata_at_root = bool(oauth_cfg.get("serve_metadata_at_root", False))

    async def metadata_endpoint(request: Request) -> JSONResponse:
        """RFC 9728 Protected Resource Metadata（認証不要）"""
        md = build_protected_resource_metadata(request, config)
        print(f"\n{'='*60}")
        print("📥 GET /.well-known/oauth-protected-resource (RFC 9728)")
        print(f"📤 {json.dumps(md, ensure_ascii=False)}")
        print(f"{'='*60}\n")
        return JSONResponse(
            md,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def root_endpoint(request: Request):
        """ルート GET。serve_metadata_at_root=true ならメタデータ、それ以外はヘルスチェック。"""
        if serve_metadata_at_root:
            return await metadata_endpoint(request)
        return JSONResponse(
            {
                "status": "ok",
                "server": "MCP Server (Streamable HTTP)",
                "version": (config.get("server_info", {}) or {}).get("version", "1.0.0"),
                "transport": "streamable-http",
                "message": "Server is running. POST to /mcp for MCP requests.",
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        # FastMCP を Mount する場合は session_manager の実行が必須
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            # メタデータ/ルートは Mount より前に（Starlette は最初にマッチしたルートが勝つ）
            Route(
                "/.well-known/oauth-protected-resource",
                metadata_endpoint,
                methods=["GET"],
            ),
            Route("/", root_endpoint, methods=["GET"]),
            # /mcp を含む全 POST は FastMCP の Streamable HTTP アプリへ
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.oauth_verifier = oauth_verifier
    app.state.serve_metadata_at_root = serve_metadata_at_root

    # ミドルウェアは追加順と逆順で実行される点に注意。
    # リクエスト通過順: CORS（外）→ GatewayPathRewrite（/ を /mcp にリライト）
    #                  → BearerAuth（/mcp を認証）→ アプリ
    app.add_middleware(BearerAuthMiddleware)
    app.add_middleware(GatewayPathRewriteMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Mcp-Protocol-Version"],
        expose_headers=["Mcp-Session-Id"],
    )

    return app, mcp


# ============================================================
# エントリポイント
# ============================================================
def main() -> None:
    config = load_config()
    ensure_data_files(config)

    # CLI 引数でポート上書き（既存 mcpServer.py と互換）
    if len(sys.argv) > 1:
        try:
            config["port"] = int(sys.argv[1])
        except ValueError:
            pass

    app, _mcp = build_app(config)

    oauth_cfg = config.get("oauth", {}) or {}
    if oauth_cfg.get("enabled"):
        print("🔒 OAuth enabled (Bearer/JWKS verification on /mcp)")
    else:
        print("🔓 OAuth disabled (no authentication)")

    host = config.get("host", "0.0.0.0")
    port = config.get("port", 9000)

    print("🚀 MCP Server (Streamable HTTP) running")
    print(f"   Listen:    http://{host}:{port}/mcp")
    print(f"   Metadata:  http://{host}:{port}/.well-known/oauth-protected-resource"
          + ("  (also at / )" if oauth_cfg.get("serve_metadata_at_root") else ""))
    print("   Protocol:  2025-06-18 (negotiated by SDK)")
    print("   Transport: Streamable HTTP (stateless, json_response)")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
