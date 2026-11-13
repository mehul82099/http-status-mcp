#!/usr/bin/env python3
"""Stdio integration tests for http-status-mcp (2026-07-28 resource templates,
cursor pagination, ref/resource completion, tools error channels)."""

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "http_status_server.py")


class Session:
    def __init__(self, page_size=20):
        env = dict(os.environ)
        env["HTTP_STATUS_MCP_PAGE_SIZE"] = str(page_size)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=HERE,
        )
        self.q = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.q.put(json.loads(line))
            except ValueError:
                self.q.put({"_unparsable": line})
        self.q.put(None)

    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout=5.0):
        return self.q.get(timeout=timeout)

    def call(self, rid, method, params=None):
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return self.recv()

    def initialize(self):
        return self.call("init", "initialize", {"protocolVersion": "2026-07-28"})

    def close(self, expect_exit=True):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        err = self.proc.stderr.read()
        if expect_exit and (self.proc.returncode != 0 or err):
            raise AssertionError("server exit=%s stderr=%r" % (self.proc.returncode, err))


class HttpStatusTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(page_size=17)  # odd size to exercise page edges

    def tearDown(self):
        self.session.close(expect_exit=False)

    # -- handshake / listing -------------------------------------------------

    def test_initialize_declares_capabilities(self):
        r = self.session.initialize()
        self.assertEqual(r["result"]["capabilities"],
                         {"resources": {}, "tools": {}, "completions": {}})

    def test_ping(self):
        self.session.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(self.session.recv()["result"], {"resultType": "complete"})

    # -- pagination -----------------------------------------------------------

    def test_resources_list_paginates_to_exact_cover(self):
        self.session.initialize()
        seen = []
        cursor = None
        pages = 0
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            r = self.session.call(100 + pages, "resources/list", params)["result"]
            self.assertEqual(r["resultType"], "complete")
            self.assertEqual(r["cacheScope"], "public")
            self.assertIn("ttlMs", r)
            self.assertLessEqual(len(r["resources"]), 17)
            self.assertTrue(r["resources"])
            seen += [x["uri"] for x in r["resources"]]
            pages += 1
            cursor = r.get("nextCursor")
            if cursor is None:
                break
            self.assertLess(pages, 20)  # safety against infinite paging
        self.assertEqual(len(seen), len(set(seen)), "duplicate URIs across pages")
        self.assertEqual(len(seen), 63)
        self.assertEqual(seen[0], "httpstatus:///100")
        self.assertEqual(seen[-1], "httpstatus:///511")

    def test_first_page_size_and_cursor_presence(self):
        self.session.initialize()
        r = self.session.call(2, "resources/list")["result"]
        self.assertEqual(len(r["resources"]), 17)
        self.assertIn("nextCursor", r)
        # Last page: 63 = 17*3 + 12 -> page 4 has 12 and no cursor
        c = r["nextCursor"]
        r2 = self.session.call(3, "resources/list", {"cursor": c})["result"]
        c = r2["nextCursor"]
        r3 = self.session.call(4, "resources/list", {"cursor": c})["result"]
        c = r3["nextCursor"]
        r4 = self.session.call(5, "resources/list", {"cursor": c})["result"]
        self.assertEqual(len(r4["resources"]), 12)
        self.assertNotIn("nextCursor", r4)

    def test_empty_string_cursor_is_valid(self):
        self.session.initialize()
        r = self.session.call(6, "resources/list", {"cursor": ""})
        self.assertIn("result", r)
        self.assertEqual(r["result"]["resources"][0]["uri"], "httpstatus:///100")

    def test_invalid_cursors_rejected(self):
        self.session.initialize()
        bad = ["!!!not-base64!!!",
               base64.urlsafe_b64encode(b"abc").decode(),          # not an int
               base64.urlsafe_b64encode(b"-1").decode(),           # negative
               base64.urlsafe_b64encode(b"999").decode()]          # out of range
        for i, c in enumerate(bad):
            r = self.session.call(10 + i, "resources/list", {"cursor": c})
            self.assertEqual(r["error"]["code"], -32602, c)

    # -- resources/read + templates -------------------------------------------

    def test_read_valid_code(self):
        self.session.initialize()
        r = self.session.call(20, "resources/read", {"uri": "httpstatus:///404"})["result"]
        self.assertEqual(r["cacheScope"], "public")
        record = json.loads(r["contents"][0]["text"])
        self.assertEqual(record["phrase"], "Not Found")
        self.assertEqual(record["category"], "client error")
        self.assertEqual(record["spec"], "RFC 9110")

    def test_read_rejects_bad_uris(self):
        self.session.initialize()
        for i, uri in enumerate(["httpstatus:///99", "httpstatus:///9999",
                                 "httpstatus:///abc", "httpstatus:///42",
                                 "http://example.com/404", "httpstatus:///",
                                 "httpstatus://///404"]):
            r = self.session.call(30 + i, "resources/read", {"uri": uri})
            self.assertEqual(r["error"]["code"], -32602, uri)

    def test_templates_list(self):
        self.session.initialize()
        r = self.session.call(40, "resources/templates/list")["result"]
        t = r["resourceTemplates"][0]
        self.assertEqual(t["uriTemplate"], "httpstatus:///{code}")

    # -- completion -------------------------------------------------------------

    def test_completion_by_code_prefix(self):
        self.session.initialize()
        r = self.session.call(50, "completion/complete", {
            "ref": {"type": "ref/resource", "uri": "httpstatus:///{code}"},
            "argument": {"name": "code", "value": "40"}})
        values = r["result"]["completion"]["values"]
        self.assertIn("404", values)
        self.assertIn("408", values)
        self.assertTrue(all(v.startswith("4") for v in values))

    def test_completion_by_phrase_prefix(self):
        self.session.initialize()
        r = self.session.call(51, "completion/complete", {
            "ref": {"type": "ref/resource", "uri": "httpstatus:///{code}"},
            "argument": {"name": "code", "value": "not"}})
        values = r["result"]["completion"]["values"]
        self.assertIn("404", values)   # Not Found
        self.assertIn("304", values)   # Not Modified
        self.assertIn("501", values)   # Not Implemented

    def test_completion_empty_prefix_returns_all(self):
        self.session.initialize()
        r = self.session.call(52, "completion/complete", {
            "ref": {"type": "ref/resource", "uri": "httpstatus:///{code}"},
            "argument": {"name": "code", "value": ""}})
        comp = r["result"]["completion"]
        self.assertEqual(comp["total"], 63)
        self.assertFalse(comp["hasMore"])
        self.assertEqual(len(comp["values"]), 63)

    def test_completion_rejects_bad_refs(self):
        self.session.initialize()
        cases = [
            {"ref": {"type": "ref/prompt", "name": "x"}, "argument": {"name": "code", "value": ""}},
            {"ref": {"type": "ref/resource", "uri": "other:///{x}"}, "argument": {"name": "code", "value": ""}},
            {"ref": {"type": "ref/resource", "uri": "httpstatus:///{code}"}, "argument": {"name": "nope", "value": ""}},
            {"ref": {"type": "ref/resource", "uri": "httpstatus:///{code}"}, "argument": {"name": "code", "value": 4}},
        ]
        for i, params in enumerate(cases):
            r = self.session.call(60 + i, "completion/complete", params)
            self.assertEqual(r["error"]["code"], -32602, i)

    # -- tools -----------------------------------------------------------------

    def test_tools_list_shape(self):
        self.session.initialize()
        r = self.session.call(70, "tools/list")["result"]
        tools = {t["name"]: t for t in r["tools"]}
        self.assertEqual(set(tools), {"status_search", "status_explain"})
        for t in tools.values():
            self.assertTrue(t["annotations"]["readOnlyHint"])
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_status_search_finds_by_phrase(self):
        self.session.initialize()
        r = self.session.call(71, "tools/call",
                              {"name": "status_search", "arguments": {"query": "timeout"}})
        sc = r["result"]["structuredContent"]
        codes = {m["code"] for m in sc["matches"]}
        self.assertIn(408, codes)
        self.assertIn(504, codes)
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), sc)

    def test_status_explain_valid_and_unknown(self):
        self.session.initialize()
        r = self.session.call(72, "tools/call",
                              {"name": "status_explain", "arguments": {"code": 418}})
        self.assertEqual(r["result"]["structuredContent"]["phrase"], "I'm a Teapot")
        r2 = self.session.call(73, "tools/call",
                               {"name": "status_explain", "arguments": {"code": 999}})
        self.assertTrue(r2["result"]["isError"])
        self.assertIn("999", r2["result"]["content"][0]["text"])

    def test_tool_error_channels(self):
        self.session.initialize()
        # wrong argument type -> execution error (isError), fixable by the model
        r = self.session.call(74, "tools/call",
                              {"name": "status_explain", "arguments": {"code": "404"}})
        self.assertTrue(r["result"]["isError"])
        # unknown tool -> protocol error
        r = self.session.call(75, "tools/call", {"name": "ghost", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)
        # extra argument -> execution error
        r = self.session.call(76, "tools/call",
                              {"name": "status_search", "arguments": {"query": "x", "evil": 1}})
        self.assertTrue(r["result"]["isError"])

    # -- dataset + protocol edges ----------------------------------------------

    def test_dataset_integrity(self):
        sys.path.insert(0, HERE)
        import http_status_server as srv
        codes = [s["code"] for s in srv.STATUSES]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(codes, sorted(codes))
        for s in srv.STATUSES:
            self.assertEqual(s["category"], srv.CATEGORY[s["code"] // 100])
            self.assertTrue(100 <= s["code"] <= 599)
            self.assertTrue(s["phrase"] and s["summary"] and s["spec"])

    def test_unknown_method_and_parse_error(self):
        self.session.initialize()
        self.session.send({"jsonrpc": "2.0", "id": 80, "method": "bogus/method"})
        self.assertEqual(self.session.recv()["error"]["code"], -32601)
        self.session.proc.stdin.write("{nope\n")
        self.session.proc.stdin.flush()
        self.assertEqual(self.session.recv()["error"]["code"], -32700)

    def test_graceful_stdin_eof(self):
        self.session.initialize()
        self.session.proc.stdin.close()
        self.assertIsNone(self.session.recv(timeout=5))
        self.session.proc.wait(timeout=10)
        self.assertEqual(self.session.proc.returncode, 0)
        self.assertEqual(self.session.proc.stderr.read(), "")


if __name__ == "__main__":
    unittest.main()
