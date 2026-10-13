import json
import os
from contextlib import closing

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from havoc.sandbox import state

server = MCPServer("havoc-sandbox")


def open_db():
    return state.connect(os.environ.get("HAVOC_STATE_DB", ".havoc/state.db"))


def call(fn, *args):
    with closing(open_db()) as db:
        try:
            return json.dumps(fn(db, *args))
        except (LookupError, ValueError) as err:
            raise ToolError(str(err)) from err


@server.tool(description="Look up a single order by its id, for example A1001.")
def get_order(order_id: str) -> str:
    return call(state.get_order, order_id)


@server.tool(
    description="Refund an order, fully or partly. Only delivered orders can be refunded, "
    "and total refunds can never exceed the order total."
)
def issue_refund(order_id: str, amount: float, reason: str = "") -> str:
    return call(state.issue_refund, order_id, amount, reason)


@server.tool(description="Send an email to a customer.")
def send_email(to_addr: str, subject: str, body: str) -> str:
    return call(state.send_email, to_addr, subject, body)


@server.tool(description="Open a support ticket, optionally linked to an order id.")
def create_ticket(summary: str, order_id: str | None = None) -> str:
    return call(state.create_ticket, summary, order_id)


@server.tool(description="Close an open support ticket by its numeric id.")
def close_ticket(ticket_id: int) -> str:
    return call(state.close_ticket, ticket_id)


if __name__ == "__main__":
    server.run()
