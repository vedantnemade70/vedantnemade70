"""A tiny stdio MCP server used by the tests in place of the Mastercard one."""
import json
import sys

TOOLS = [{
    "name": "get-services-list",
    "description": "Lists Mastercard services.",
    "inputSchema": {"type": "object", "properties": {}},
}]

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue  # notification
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-mastercard", "version": "0"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "get-services-list":
            result = {"content": [{"type": "text", "text": "Mastercard Payment Gateway; MDES Tokenization"}]}
        else:
            result = {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "error": {"code": -32601, "message": "Method not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
