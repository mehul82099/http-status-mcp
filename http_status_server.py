#!/usr/bin/env python3
"""http-status-mcp: MCP stdio server (protocol version 2026-07-28) serving a
reference database of HTTP status codes, built to demonstrate three spec
surfaces that most servers skip:

  - RESOURCE TEMPLATES: resources/templates/list advertises
    httpstatus:///{code}; resources/read resolves expanded URIs
  - CURSOR PAGINATION: resources/list pages the database with an opaque
    nextCursor (invalid cursors -> -32602; empty string is a valid cursor)
  - ref/resource COMPLETION: completion/complete completes the {code}
    template variable by code prefix OR phrase prefix

Plus a small tools surface (status_search, status_explain) with the
two-channel error model. Zero dependencies, single file, Python 3.9+.

Environment:
  HTTP_STATUS_MCP_PAGE_SIZE   resources/list page size (default: 20)
"""

import base64
import json
import os
import re
import sys

LATEST_PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSIONS = ["2026-07-28", "2026-05-12", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

PAGE_SIZE = max(1, int(os.environ.get("HTTP_STATUS_MCP_PAGE_SIZE", "20")))

MAX_COMPLETION_VALUES = 100
URI_PREFIX = "httpstatus:///"
URI_TEMPLATE = "httpstatus:///{code}"
CACHE_TTL_MS = 86400000  # static reference data: 24h

CATEGORY = {1: "informational", 2: "success", 3: "redirection",
            4: "client error", 5: "server error"}

# code, phrase, one-line summary, spec reference
_DATA = [
    (100, "Continue", "The server has received the request headers and the client should proceed to send the request body.", "RFC 9110"),
    (101, "Switching Protocols", "The server agrees to switch protocols as requested by the client's Upgrade header.", "RFC 9110"),
    (102, "Processing", "The server has received and is processing the request, but no response is available yet.", "RFC 2518 (WebDAV)"),
    (103, "Early Hints", "Hints about headers the final response will carry, sent while the server prepares it.", "RFC 8297"),
    (200, "OK", "The request succeeded; the payload depends on the method.", "RFC 9110"),
    (201, "Created", "The request succeeded and a new resource was created as a result.", "RFC 9110"),
    (202, "Accepted", "The request was accepted for processing, but processing is not complete.", "RFC 9110"),
    (203, "Non-Authoritative Information", "The payload was transformed from the origin server's response by a proxy.", "RFC 9110"),
    (204, "No Content", "The request succeeded and there is no content to send in the response.", "RFC 9110"),
    (205, "Reset Content", "The request succeeded; the client should reset the document view that caused the request.", "RFC 9110"),
    (206, "Partial Content", "The server is delivering part of the selected representation, as requested via Range.", "RFC 9110"),
    (207, "Multi-Status", "Multiple status codes for multiple independent operations, in a single XML body.", "RFC 4918 (WebDAV)"),
    (208, "Already Reported", "Members of a DAV binding were already enumerated in an earlier part of the response.", "RFC 5842"),
    (226, "IM Used", "The response is a delta (instance manipulation) applied to the current instance.", "RFC 3229"),
    (300, "Multiple Choices", "The target has more than one representation; the user agent should pick one.", "RFC 9110"),
    (301, "Moved Permanently", "The target has a new permanent URI; future references should use the new URI.", "RFC 9110"),
    (302, "Found", "The target resides temporarily at a different URI.", "RFC 9110"),
    (303, "See Other", "Redirect to a different URI, retrieved with GET, for this request's result.", "RFC 9110"),
    (304, "Not Modified", "The cached representation is still valid; no payload is sent.", "RFC 9110"),
    (305, "Use Proxy", "Deprecated: the requested resource must be accessed through the given proxy.", "RFC 9110 (deprecated)"),
    (306, "(Unused)", "Reserved; was used by a previous version of HTTP/1.1.", "RFC 9110"),
    (307, "Temporary Redirect", "Redirect temporarily; the method and body MUST NOT change.", "RFC 9110"),
    (308, "Permanent Redirect", "Redirect permanently; the method and body MUST NOT change.", "RFC 9110"),
    (400, "Bad Request", "The server cannot process the request due to malformed syntax or framing.", "RFC 9110"),
    (401, "Unauthorized", "Authentication credentials are missing or invalid for the target resource.", "RFC 9110"),
    (402, "Payment Required", "Reserved for future use.", "RFC 9110"),
    (403, "Forbidden", "The server understood the request but refuses to authorize it.", "RFC 9110"),
    (404, "Not Found", "No representation was found for the target URI.", "RFC 9110"),
    (405, "Method Not Allowed", "The method is known but not supported for the target resource.", "RFC 9110"),
    (406, "Not Acceptable", "No representation matches the client's proactive content negotiation headers.", "RFC 9110"),
    (407, "Proxy Authentication Required", "Authentication with the proxy is required first.", "RFC 9110"),
    (408, "Request Timeout", "The server did not receive a complete request in time.", "RFC 9110"),
    (409, "Conflict", "The request conflicts with the current state of the target resource.", "RFC 9110"),
    (410, "Gone", "The target is permanently unavailable and this condition is expected to persist.", "RFC 9110"),
    (411, "Length Required", "The request is rejected because Content-Length is missing.", "RFC 9110"),
    (412, "Precondition Failed", "One or more precondition headers evaluated to false.", "RFC 9110"),
    (413, "Content Too Large", "The request content is larger than the server will process.", "RFC 9110"),
    (414, "URI Too Long", "The request target is longer than the server will interpret.", "RFC 9110"),
    (415, "Unsupported Media Type", "The request content type is not supported for this resource.", "RFC 9110"),
    (416, "Range Not Satisfiable", "The Range header cannot be satisfied by the current representation.", "RFC 9110"),
    (417, "Expectation Failed", "The expectation in the Expect header cannot be met.", "RFC 9110"),
    (418, "I'm a Teapot", "An April Fools' joke from 1998 that refuses to brew coffee; reserved and beloved.", "RFC 2324 / RFC 9110"),
    (421, "Misdirected Request", "The request was directed at a server unable to produce a response for this authority.", "RFC 9110"),
    (422, "Unprocessable Content", "The request was well-formed but has semantic errors.", "RFC 9110"),
    (423, "Locked", "The source or destination resource of the method is locked.", "RFC 4918 (WebDAV)"),
    (424, "Failed Dependency", "The method failed because a prior method it depends on failed.", "RFC 4918 (WebDAV)"),
    (425, "Too Early", "The server is unwilling to process a request that might be replayed (0-RTT).", "RFC 8470"),
    (426, "Upgrade Required", "The client should switch to a different protocol as given in the Upgrade header.", "RFC 9110"),
    (428, "Precondition Required", "The server requires the request to be conditional.", "RFC 6585"),
    (429, "Too Many Requests", "The user has sent too many requests in a given time (rate limiting).", "RFC 6585"),
    (431, "Request Header Fields Too Large", "The header fields, individually or in total, are too large.", "RFC 6585"),
    (451, "Unavailable For Legal Reasons", "The resource is blocked for legal reasons; named after Fahrenheit 451.", "RFC 7725"),
    (500, "Internal Server Error", "The server hit an unexpected condition that prevented it from answering.", "RFC 9110"),
    (501, "Not Implemented", "The server does not support the functionality required to fulfill the request.", "RFC 9110"),
    (502, "Bad Gateway", "A gateway or proxy received an invalid response from upstream.", "RFC 9110"),
    (503, "Service Unavailable", "The server is temporarily overloaded or down for maintenance.", "RFC 9110"),
    (504, "Gateway Timeout", "A gateway or proxy did not get an upstream response in time.", "RFC 9110"),
    (505, "HTTP Version Not Supported", "The server does not support the HTTP version used in the request.", "RFC 9110"),
    (506, "Variant Also Negotiates", "Transparent content negotiation for the request results in a circular reference.", "RFC 2295"),
    (507, "Insufficient Storage", "The server cannot store the representation needed to complete the request.", "RFC 4918 (WebDAV)"),
    (508, "Loop Detected", "An infinite loop was detected while processing a DAV: bind operation.", "RFC 5842"),
    (510, "Not Extended", "Further extensions to the request are required for the server to fulfill it.", "RFC 2774"),
    (511, "Network Authentication Required", "The client must authenticate to gain network access (captive portals).", "RFC 6585"),
]

STATUSES = [
    {"code": c, "phrase": p, "category": CATEGORY[c // 100], "summary": s, "spec": r}
    for (c, p, s, r) in _DATA
]
BY_CODE = {s["code"]: s for s in STATUSES}


def _emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _error(rid, code, message):
    _emit({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _result(rid, result):
    _emit({"jsonrpc": "2.0", "id": rid, "result": result})


# ---------------------------------------------------------------------------
# Opaque cursors (base64 of the decimal offset; clients treat as opaque)

def _encode_cursor(offset):
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor):
    """Return offset int, or None if invalid. Empty string is a valid cursor
    (spec) and means the start."""
    if cursor == "":
        return 0
    if not isinstance(cursor, str):
        return None
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except Exception:
        return None
    if offset < 0 or offset >= len(STATUSES):
        return None
    return offset


# ---------------------------------------------------------------------------
# Resources

def handle_resources_list(rid, params):
    cursor = params.get("cursor") if isinstance(params, dict) else None
    offset = 0
    if cursor is not None:
        offset = _decode_cursor(cursor)
        if offset is None:
            _error(rid, -32602, "Invalid params: invalid cursor")
            return
    page = STATUSES[offset: offset + PAGE_SIZE]
    resources = [{
        "uri": URI_PREFIX + str(s["code"]),
        "name": "%d %s" % (s["code"], s["phrase"]),
        "description": s["summary"],
        "mimeType": "application/json",
    } for s in page]
    result = {"resultType": "complete", "resources": resources,
              "ttlMs": CACHE_TTL_MS, "cacheScope": "public"}
    end = offset + PAGE_SIZE
    if end < len(STATUSES):
        result["nextCursor"] = _encode_cursor(end)
    _result(rid, result)


def handle_resources_read(rid, params):
    uri = params.get("uri") if isinstance(params, dict) else None
    if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
        _error(rid, -32602, "Invalid params: uri must start with %s" % URI_PREFIX)
        return
    tail = uri[len(URI_PREFIX):]
    if not re.fullmatch(r"\d{3}", tail or ""):
        _error(rid, -32602, "Invalid params: expected %s<code>" % URI_PREFIX)
        return
    status = BY_CODE.get(int(tail))
    if status is None:
        _error(rid, -32602, "Invalid params: unknown status code: %s" % tail)
        return
    _result(rid, {
        "resultType": "complete",
        "ttlMs": CACHE_TTL_MS,
        "cacheScope": "public",
        "contents": [{
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(status, ensure_ascii=False),
        }],
    })


def handle_templates_list(rid):
    _result(rid, {"resultType": "complete", "resourceTemplates": [{
        "uriTemplate": URI_TEMPLATE,
        "name": "HTTP Status",
        "title": "HTTP Status Code",
        "description": "Reference record for one HTTP status code (e.g. httpstatus:///404)",
        "mimeType": "application/json",
    }]})


# ---------------------------------------------------------------------------
# Completion (ref/resource template variable completion)

def handle_complete(rid, params):
    if not isinstance(params, dict):
        _error(rid, -32602, "Invalid params: completion/complete requires a params object")
        return
    ref = params.get("ref")
    argument = params.get("argument")
    if (not isinstance(ref, dict) or ref.get("type") != "ref/resource"
            or not isinstance(ref.get("uri"), str)):
        _error(rid, -32602, "Invalid params: ref must be a ref/resource with a uri")
        return
    if ref["uri"] != URI_TEMPLATE:
        _error(rid, -32602, "Invalid params: unknown resource template: %s" % ref["uri"])
        return
    if (not isinstance(argument, dict) or not isinstance(argument.get("name"), str)
            or not isinstance(argument.get("value"), str)):
        _error(rid, -32602, "Invalid params: argument must have string name and value")
        return
    if argument["name"] != "code":
        _error(rid, -32602, "Invalid params: unknown template variable: %s" % argument["name"])
        return
    prefix = argument["value"].lower()
    values = [str(s["code"]) for s in STATUSES
              if str(s["code"]).startswith(prefix) or s["phrase"].lower().startswith(prefix)]
    total = len(values)
    values = values[:MAX_COMPLETION_VALUES]
    _result(rid, {"resultType": "complete",
                  "completion": {"values": values, "total": total,
                                 "hasMore": total > len(values)}})


# ---------------------------------------------------------------------------
# Tools (two-channel error model: -32602 protocol, isError execution)

class ToolError(Exception):
    pass


READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
             "idempotentHint": True, "openWorldHint": False}


def t_status_search(args):
    q = args["query"].lower()
    matches = [s for s in STATUSES
               if q in str(s["code"]) or q in s["phrase"].lower() or q in s["summary"].lower()]
    return {"query": args["query"], "count": len(matches), "matches": matches[:20]}


def t_status_explain(args):
    status = BY_CODE.get(args["code"])
    if status is None:
        raise ToolError("No record for status code %d - valid codes are the %d standard codes in the database" % (args["code"], len(STATUSES)))
    return status


def _validate_args(args, spec):
    problems = []
    for k, rule in spec.items():
        if rule.get("required") and k not in args:
            problems.append("missing required argument '%s'" % k)
        elif k in args:
            v = args[k]
            t = rule["type"]
            if t == "string" and not isinstance(v, str):
                problems.append("%s: expected string" % k)
            elif t == "integer" and (not isinstance(v, int) or isinstance(v, bool)):
                problems.append("%s: expected integer" % k)
    for k in args:
        if k not in spec:
            problems.append("unexpected argument '%s'" % k)
    return problems


TOOLS = {
    "status_search": {
        "title": "Status Search",
        "description": "Search HTTP status codes by code fragment, phrase, or summary text.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string", "description": "Search text, e.g. 'timeout' or '40'"}},
                        "required": ["query"], "additionalProperties": False},
        "outputSchema": {"type": "object",
                         "properties": {"query": {"type": "string"},
                                        "count": {"type": "integer"},
                                        "matches": {"type": "array"}},
                         "required": ["query", "count", "matches"]},
        "annotations": dict(READ_ONLY),
        "args": {"query": {"type": "string", "required": True}},
        "fn": t_status_search,
    },
    "status_explain": {
        "title": "Status Explain",
        "description": "Return the full reference record for one HTTP status code.",
        "inputSchema": {"type": "object",
                        "properties": {"code": {"type": "integer", "description": "Status code, e.g. 404"}},
                        "required": ["code"], "additionalProperties": False},
        "outputSchema": {"type": "object",
                         "properties": {"code": {"type": "integer"},
                                        "phrase": {"type": "string"},
                                        "category": {"type": "string"},
                                        "summary": {"type": "string"},
                                        "spec": {"type": "string"}},
                         "required": ["code", "phrase", "category", "summary", "spec"]},
        "annotations": dict(READ_ONLY),
        "args": {"code": {"type": "integer", "required": True}},
        "fn": t_status_explain,
    },
}


def handle_tools_list(rid):
    _result(rid, {"resultType": "complete", "tools": [
        {"name": name, "title": t["title"], "description": t["description"],
         "inputSchema": t["inputSchema"], "outputSchema": t["outputSchema"],
         "annotations": t["annotations"]}
        for name, t in TOOLS.items()
    ]})


def handle_tools_call(rid, params):
    if not isinstance(params, dict):
        _error(rid, -32602, "Invalid params: tools/call requires a params object")
        return
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        _error(rid, -32602, "Invalid params: name must be a string")
        return
    if not isinstance(arguments, dict):
        _error(rid, -32602, "Invalid params: arguments must be an object")
        return
    tool = TOOLS.get(name)
    if tool is None:
        _error(rid, -32602, "Unknown tool: %s" % name)
        return
    problems = _validate_args(arguments, tool["args"])
    if problems:
        _result(rid, {"resultType": "complete", "isError": True,
                      "content": [{"type": "text", "text": "Invalid arguments: " + "; ".join(problems)}]})
        return
    try:
        structured = tool["fn"](arguments)
    except ToolError as e:
        _result(rid, {"resultType": "complete", "isError": True,
                      "content": [{"type": "text", "text": str(e)}]})
        return
    _result(rid, {"resultType": "complete", "isError": False,
                  "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
                  "structuredContent": structured})


# ---------------------------------------------------------------------------
# Dispatch

def handle_request(req):
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params")

    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = requested if requested in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        _result(rid, {
            "resultType": "complete",
            "protocolVersion": version,
            "capabilities": {"resources": {}, "tools": {}, "completions": {}},
            "serverInfo": {"name": "http-status-mcp", "version": "1.0.0"},
        })
    elif method == "ping":
        _result(rid, {"resultType": "complete"})
    elif method == "resources/list":
        handle_resources_list(rid, params if isinstance(params, dict) else {})
    elif method == "resources/read":
        handle_resources_read(rid, params)
    elif method == "resources/templates/list":
        handle_templates_list(rid)
    elif method == "completion/complete":
        handle_complete(rid, params)
    elif method == "tools/list":
        handle_tools_list(rid)
    elif method == "tools/call":
        handle_tools_call(rid, params)
    elif method == "notifications/initialized":
        pass
    elif rid is None:
        pass
    else:
        _error(rid, -32601, "Method not found: %s" % method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "Parse error")
            continue
        if not isinstance(req, dict) or "method" not in req:
            _error(req.get("id") if isinstance(req, dict) else None, -32600, "Invalid Request")
            continue
        handle_request(req)


if __name__ == "__main__":
    main()
