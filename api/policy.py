import re

CAN = {
    "admin": {"sql", "etl"},
    "analyst": {"sql", "etl"},
    "viewer": {"sql"},
}

# level: public < internal < sensitive < highly_sensitive
COLUMNS = {
    "users.email": "sensitive",
    "users.phone": "highly_sensitive",
    "payments.amount": "internal",
}

_RANK = {"public": 0, "internal": 1, "sensitive": 2, "highly_sensitive": 3}
_MAX = {"admin": 3, "analyst": 2, "viewer": 1}  # viewer: public + internal only

_OTHER = re.compile(r"tenant_id\s*=\s*'([^']+)'", re.I)


def assert_can(role: str, action: str) -> None:
    if action not in CAN.get(role, ()):
        raise PermissionError(action)


def route_target(route_response: str, role: str) -> str:
    """Pick the graph node only after the role is allowed to run that action."""
    if route_response == "sql":
        node = "sql_node"
    elif route_response == "etl":
        node = "etl_node"
    else:
        raise ValueError(f"Invalid route response: {route_response}")
    assert_can(role, route_response)
    return node


def mentions_other_tenant(question: str, tenant_id: str) -> bool:
    return any(found != tenant_id for found in _OTHER.findall(question))


def column_visible(role: str, table: str, column: str) -> bool:
    level = COLUMNS.get(f"{table}.{column}", "internal")
    return _RANK[level] <= _MAX.get(role, 1)


def mask(role: str, table: str, column: str, value):
    level = COLUMNS.get(f"{table}.{column}", "internal")
    if role == "admin" or _RANK[level] <= 1:
        return value
    return None


def mask_cell(role: str, column: str, value, table: str = ""):
    """Mask one result cell. Unknown table uses the strictest classified level for that column name."""
    if table:
        return mask(role, table, column, value)
    ranks = [_RANK[COLUMNS[key]] for key in COLUMNS if key.rsplit(".", 1)[-1] == column]
    if not ranks or role == "admin" or max(ranks) <= 1:
        return value
    return None
