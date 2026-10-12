"""Finance MCP server: sql and propose_migration over stdio.

Run with `python -m ft_mcp`. Apply and reject stay on `ft-migration`.
mcp 2 renamed FastMCP to MCPServer; this is that high-level server.
"""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ft_mcp.proposals import MigrationError, propose_migration as propose
from ft_mcp.sql_tool import QueryFailed, run_sql

INSTRUCTIONS = """
Finance ledger, two tools.

sql runs as the agent database role inside one transaction. Put several
statements in one call when they must commit together, such as a transaction
and its postings. The call sets app.run_id from the APP_RUN_ID environment
variable when the bot has set it. Schema changes are rejected by the role.

propose_migration writes db/migrations/NNN_name.sql with status pending and
dry-runs the SQL in a transaction that is rolled back. The result says whether
the change is additive or destructive. Additive means only CREATE TABLE,
CREATE INDEX, CREATE VIEW, or ADD COLUMN that is nullable or has a default.
DROP, RENAME, ALTER TYPE, SET NOT NULL, and anything else are destructive.
AUTO_APPLY_ADDITIVE defaults to false. When it is true, only an additive
proposal is applied. Destructive proposals always stay pending. Applying and
rejecting a pending proposal is not a tool.
"""

mcp = MCPServer("finance", instructions=INSTRUCTIONS, log_level="WARNING")


@mcp.tool(structured_output=False)
def sql(
    query: Annotated[
        str,
        Field(
            description=(
                "SQL text. Semicolon-separated statements run in one transaction "
                "as the agent role."
            )
        ),
    ],
) -> str:
    """Query or change ledger data. Returns JSON rows, row counts, and a truncation note.

    On failure the tool result is the Postgres error, including the statement
    that failed. The transaction is rolled back. There is a statement timeout.
    """
    try:
        payload = run_sql(query)
    except QueryFailed as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(structured_output=False)
def propose_migration(
    name: Annotated[str, Field(description="Short snake_case name for the migration file.")],
    up_sql: Annotated[str, Field(description="Migration SQL. Do not include BEGIN or COMMIT.")],
    reason: Annotated[str, Field(description="One line explaining why this change is needed.")],
) -> str:
    """Write a pending migration, dry-run it, and classify it additive or destructive.

    The dry-run is rolled back. A failed dry-run writes no file. The result
    includes the migration id, file path, classification, and whether it was
    auto-applied.
    """
    try:
        payload = propose(name, up_sql, reason)
    except MigrationError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    mcp.run(transport="stdio")
