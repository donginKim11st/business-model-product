# MCP 서버 — 상품 정체성 그래프

우리 엔진을 **MCP 도구**로 노출해, Claude(데스크톱/코드 등 MCP 클라이언트)에서
대화로 호출합니다. 시스템 Python 3.9.6에서 작동하도록 **공식 SDK 없이 stdlib만**으로
stdio JSON-RPC를 직접 구현 (의존성 0).

## 도구 3개

| 도구 | 하는 일 | 키 필요 |
|---|---|---|
| `resolve_products` | 여러 쇼핑 리스팅을 '같은 제품'으로 묶음(크로스마켓 통합) | ✗ |
| `analyze_brand` | 네이버 쇼핑 실데이터 수집→통합→공식가보다 싸게 팔리는 제품 리포트(1개당) | ✓ |
| `seller_footprint` | 판매처별로 그 브랜드를 몇 개·평균 몇 % 싸게 파는지 | ✓ |

`analyze_brand` / `seller_footprint` 는 환경변수 `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` 필요.
`resolve_products` 는 키 없이 바로 작동.

## Claude Code 에서 쓰기

프로젝트에 이미 `.mcp.json` 이 있습니다. 이 폴더에서 Claude Code를 (재)시작하면
프로젝트 MCP 서버 승인 프롬프트가 뜹니다 → 승인하면 `/mcp` 에 나타납니다.

직접 등록도 가능:
```bash
claude mcp add --transport stdio product-identity-graph \
  -- /usr/bin/python3 /Users/a1101417/Work/business-model/identity/mcp_server.py
```
네이버 키는 셸 환경변수로 export 해두면 `.mcp.json` 의 `${NAVER_CLIENT_ID}` 로 전달됩니다.

## Claude Desktop 에서 쓰기 (macOS)

`~/Library/Application Support/Claude/claude_desktop_config.json` 에 추가:
```json
{
  "mcpServers": {
    "product-identity-graph": {
      "command": "/usr/bin/python3",
      "args": ["/Users/a1101417/Work/business-model/identity/mcp_server.py"],
      "env": {
        "NAVER_CLIENT_ID": "여기에-아이디",
        "NAVER_CLIENT_SECRET": "여기에-시크릿"
      }
    }
  }
}
```
저장 후 Claude Desktop 재시작. (Desktop은 `${}` 확장이 불안정하니 실제 값을 넣거나,
키 없이 `resolve_products` 만 쓸 거면 `env` 생략.)

## 써보기 (예시 프롬프트)

- "resolve_products로 이 리스팅들 같은 제품끼리 묶어줘: [네이버 '소니 WH-1000XM5 헤드폰', 아마존 'Sony WH-1000XM5 Headphones', 쿠팡 '소니 WH-1000XM4 헤드폰']"
- "analyze_brand 로 싸이닉이 공식가보다 싸게 팔리는 제품 알려줘"
- "seller_footprint 로 싸이닉을 어떤 판매처가 가장 싸게 파는지 보여줘"

## 직접 프로토콜 테스트 (클라이언트 없이)

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python3 mcp_server.py
```

## 주의

- stdout은 JSON-RPC 전용. 모든 로그는 stderr.
- 시스템 Python 3.9 호환(공식 SDK는 3.10+ 필요). Python 3.10+ 면 `pip install "mcp[cli]"` 후 FastMCP로 더 간단히 재작성 가능.
- 네이버 키는 파일에 하드코딩하지 말고 환경변수/`${}` 로.
