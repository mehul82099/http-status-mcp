# http-status-mcp

A zero-dependency, single-file MCP stdio server (protocol version
`2026-07-28`) serving a reference database of all 63 standard HTTP status
codes - built to demonstrate three spec surfaces that most servers skip:

- **Resource templates**: `resources/templates/list` advertises
  `httpstatus:///{code}`; `resources/read` resolves expanded URIs like
  `httpstatus:///404` into the full record
- **Cursor pagination**: `resources/list` pages the database with an opaque
  `nextCursor` (invalid cursors are rejected with `-32602`; an empty string
  is honored as a valid cursor, per spec)
- **ref/resource completion**: `completion/complete` completes the `{code}`
  template variable by code prefix *or* phrase prefix (`40` -> `404`, `408`;
  `not` -> `404 Not Found`, `304 Not Modified`, `501 Not Implemented`)

Plus a small tools surface (`status_search`, `status_explain`) with the
spec's two-channel error model, and `ttlMs`/`cacheScope` caching hints on
static data. No packages, no build step.

## Usage

```bash
python3 http_status_server.py
HTTP_STATUS_MCP_PAGE_SIZE=50 python3 http_status_server.py
```

Example session:

```json
→ {"jsonrpc":"2.0","id":1,"method":"resources/list"}
← {"jsonrpc":"2.0","id":1,"result":{"resultType":"complete","resources":[{"uri":"httpstatus:///100","name":"100 Continue",...},...],"nextCursor":"MTc=","ttlMs":86400000,"cacheScope":"public"}}
→ {"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":"httpstatus:///418"}}
← {"jsonrpc":"2.0","id":2,"result":{"resultType":"complete","contents":[{"uri":"httpstatus:///418","mimeType":"application/json","text":"{\"code\": 418, \"phrase\": \"I'm a Teapot\", ...}"}],...}}
→ {"jsonrpc":"2.0","id":3,"method":"completion/complete","params":{"ref":{"type":"ref/resource","uri":"httpstatus:///{code}"},"argument":{"name":"code","value":"tea"}}}
← {"jsonrpc":"2.0","id":3,"result":{"resultType":"complete","completion":{"values":[],"total":0,"hasMore":false}}}   (try "not" -> ["404","304","501"])
```

## The database

63 status codes: 1xx informational, 2xx success, 3xx redirection, 4xx client
error, 5xx server error - each with the current RFC 9110 phrase (including
the 9110 renames like `413 Content Too Large` and `422 Unprocessable
Content`), a one-line summary, and its defining specification
(RFC 9110 / 8297 / 6585 / 4918 WebDAV / 5842 / 8470 / 7725 / ...).

## Spec mechanics implemented (2026-07-28)

| Surface | Behavior here |
| --- | --- |
| Pagination | Opaque `nextCursor` (base64 offset); server-chosen page size (`HTTP_STATUS_MCP_PAGE_SIZE`, default 20); missing `nextCursor` = end; invalid cursors -> `-32602`; empty-string cursor = valid start |
| Resource templates | One advertised template `httpstatus:///{code}`; reads validate the expansion strictly (3-digit, known code) |
| Completion | `ref/resource` only; unknown template URI or variable name -> `-32602`; envelope carries `values`, `total`, `hasMore`, capped at 100 |
| Tools | Two-channel errors: unknown tool / malformed request -> `-32602`; bad argument values -> `isError: true` with a model-correctable message |
| Caching | Static records return `ttlMs` + `cacheScope: "public"` on list and read |

Interpretation notes (the spec leaves these open):

- Phrase-prefix completion maps to the code value (`not` suggests `404`),
  since the template variable is the code.
- The tool set and database are static, so no `listChanged` capability is
  declared and `subscriptions/listen` is not implemented.
- Cursor stability assumes the static database never changes mid-session
  (true here by construction).

## Tests

```bash
python3 -m unittest -v
```

20 stdio integration tests (Python 3.9+, stdlib only) spawn the server as a
subprocess and cover: capability shape; exact-cover pagination across 4 pages
with an odd page size (no duplicates, correct boundaries); empty-string and
four kinds of invalid cursors; strict read-URI validation; template
advertising; code- and phrase-prefix completion; both tool error channels;
dataset integrity (sorted, unique, category-consistent); `-32601`/`-32700`;
and clean shutdown. CI runs the suite on ubuntu-24.04 and windows-latest
against Python 3.9 and 3.13.

## Limits (honest)

- stdio transport only; records are JSON-in-text content (no embedded
  resource content type).
- Tested against the 2026-07-28 spec text with subprocess-level stdio
  integration tests; not smoke-tested against a specific desktop MCP client.
- The database is hand-maintained reference data (63 codes), not a live IANA
  registry mirror.
- Python 3.9+. No third-party packages.

## Why this exists (gap check)

GitHub repository searches for `mcp server uri template resources`
(api.github.com, November 13, 2026) returned zero repositories, and
`mcp server http status codes reference` returned one general reference-data
server. This repo is a use case for two 2026-07-28 surfaces that rarely get
implemented - resource templates with ref/resource completion, and real
cursor pagination - in one readable file over data everyone recognizes.

## Sources

- Pagination:
  https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination
- Resources (templates):
  https://modelcontextprotocol.io/specification/2026-07-28/server/resources
- Completion:
  https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/completion
- Tools:
  https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- MCP specification index: https://modelcontextprotocol.io/llms.txt

## License

MIT (c) 2026 Mehul Jhabak
