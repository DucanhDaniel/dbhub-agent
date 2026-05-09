# d:\projects\test\dbhub-agent\agent.py
"""
Superset MCP Agent with cost optimization:
  1. Tiered Models  — cheap router → expensive executor only when needed
  2. Tool Filtering — load only relevant tools per query category
  3. Description Trimming — cap tool descriptions to save context tokens
"""
import asyncio
import json
import math
import os
import time
import warnings
from typing import Optional, Type, Any

import httpx
import jwt
from langchain_openai import ChatOpenAI
from langchain_core.tools import BaseTool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, create_model
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv(override=True)

MCP_URL = os.getenv("MCP_URL", "http://localhost:5008/mcp")

# Max chars for tool descriptions (saves ~40% context tokens)
MAX_TOOL_DESC_LEN = 150


# ---------------------------------------------------------------------------
# JWT Token Management
# ---------------------------------------------------------------------------

_jwt_token: str | None = None
_jwt_expiry: float = 0


def _generate_jwt_token() -> str:
    """Ký JWT token dùng config từ .env, cache token cho đến khi gần hết hạn."""
    global _jwt_token, _jwt_expiry

    # Refresh nếu token còn hơn 60s
    if _jwt_token and time.time() < (_jwt_expiry - 60):
        return _jwt_token

    secret = os.getenv("MCP_JWT_SECRET")
    if not secret:
        raise RuntimeError("MCP_JWT_SECRET chưa được cấu hình trong .env")

    algorithm = os.getenv("MCP_JWT_ALGORITHM", "HS256")
    issuer = os.getenv("MCP_JWT_ISSUER", "dbhub")
    audience = os.getenv("MCP_JWT_AUDIENCE", "superset")
    subject = os.getenv("MCP_JWT_SUBJECT", "admin")
    expiry_hours = int(os.getenv("MCP_JWT_EXPIRY_HOURS", "24"))

    now = time.time()
    exp = now + expiry_hours * 3600

    payload = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "exp": int(exp),
        "iat": int(now),
        "scope": "mcp:read mcp:write",
    }

    _jwt_token = jwt.encode(payload, secret, algorithm=algorithm)
    _jwt_expiry = exp
    print(f"[JWT] Token generated for user '{subject}', expires in {expiry_hours}h")
    return _jwt_token


# ---------------------------------------------------------------------------
# MCP Transport (unchanged)
# ---------------------------------------------------------------------------

def parse_sse_events(text: str) -> list[dict]:
    events = []
    current_data = None
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("data:"):
            current_data = line[len("data:"):].strip()
        elif line == "" and current_data:
            try:
                events.append(json.loads(current_data))
            except json.JSONDecodeError:
                pass
            current_data = None
    if current_data:
        try:
            events.append(json.loads(current_data))
        except json.JSONDecodeError:
            pass
    if not events:
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            pass
    return events


def find_result_event(events: list[dict]) -> tuple[dict, list[str]]:
    result = {}
    notifications = []
    for event in events:
        if "error" in event:
            result = event
            notifications.append(f"RPC Error: {event['error']}")
        if "result" in event:
            result = event
        if "method" in event:
            msg = event.get("params", {}).get("data", {}).get("msg", "")
            if msg:
                print(f"  [NOTIFICATION] {msg}")
                if "error" in msg.lower() or "fail" in msg.lower():
                    notifications.append(msg)
    return result, notifications


_mcp_lock = asyncio.Lock()

async def call_mcp(method: str, params: dict) -> tuple[dict, list[str]]:
    max_retries = 3
    base_delay = 2.0
    
    async with _mcp_lock:
        token = _generate_jwt_token()
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    payload = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "Authorization": f"Bearer {token}",
                    }
                    response = await client.post(MCP_URL, json=payload, headers=headers)
                    events = parse_sse_events(response.text)
                    result, notifications = find_result_event(events)
                    if not result:
                        print(f"  [WARNING] No result. Raw: {response.text[:200]}")
                    return result, notifications
            except Exception as e:
                print(f"  [ERROR] Lần thử {attempt + 1}/{max_retries} thất bại: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                else:
                    return {}, [f"HTTP/Network Error: All {max_retries} connection attempts failed. Last error: {str(e)}"]


def extract_text(result: dict) -> str:
    if "error" in result:
        return f"RPC Error: {json.dumps(result['error'])}"
    content = result.get("result", {}).get("content", [])
    all_texts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        raw = block.get("text", "")
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            all_texts.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        except (json.JSONDecodeError, TypeError):
            all_texts.append(raw)
    return "\n".join(all_texts)


# ---------------------------------------------------------------------------
# Pagination (unchanged)
# ---------------------------------------------------------------------------

async def fetch_all_pages(
    tool_name: str,
    has_wrapper: bool,
    base_args: dict,
    page_size: int = 20
) -> str:
    all_items = []
    page = 1
    total_pages = 1
    list_keys = ["datasets", "charts", "dashboards", "databases", "items", "result", "data"]

    while page <= total_pages:
        args = {**base_args, "page": page, "page_size": page_size}
        arguments = {"request": args} if has_wrapper else args

        result, _ = await call_mcp("tools/call", {
            "name": "call_tool",
            "arguments": {"name": tool_name, "arguments": arguments}
        })
        text = extract_text(result)
        if not text:
            break

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text

        if page == 1:
            total_count = data.get("total_count") or data.get("count") or 0
            total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
            print(f"  [PAGINATE] {tool_name}: total={total_count}, pages={total_pages}")

        collected = False
        for key in list_keys:
            if key in data and isinstance(data[key], list):
                all_items.extend(data[key])
                collected = True
                print(f"  [PAGINATE] Page {page}/{total_pages}: +{len(data[key])} items → total={len(all_items)}")
                break

        if not collected:
            return text

        page += 1

    return json.dumps(all_items, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Schema Analysis & Type Coercion (unchanged)
# ---------------------------------------------------------------------------

def analyze_schema(input_schema: dict) -> tuple[bool, dict, list]:
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    if (
        list(properties.keys()) == ["request"]
        and isinstance(properties.get("request"), dict)
        and properties["request"].get("type") == "object"
    ):
        inner = properties["request"]
        return True, inner.get("properties", {}), inner.get("required", [])

    return False, properties, required


def json_type_to_python(json_type: str, is_required: bool):
    mapping = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "object": dict, "array": list,
    }
    base = mapping.get(json_type, Any)
    return base if is_required else Optional[base]


def build_pydantic_model(tool_name: str, properties: dict, required_fields: list) -> Type[BaseModel]:
    if not properties:
        return create_model(f"{tool_name}_args")
    fields = {}
    for prop_name, prop_def in properties.items():
        is_required = prop_name in required_fields
        python_type = json_type_to_python(prop_def.get("type", "string"), is_required)
        description = prop_def.get("description", prop_name)
        default = prop_def.get("default", None)
        if is_required:
            fields[prop_name] = (python_type, Field(description=description))
        else:
            fields[prop_name] = (python_type, Field(default=default, description=description))
    return create_model(f"{tool_name}_args", **fields)


def coerce_args(kwargs: dict, properties: dict) -> dict:
    coerced = {}
    for k, v in kwargs.items():
        if v is None:
            continue
        prop_def = properties.get(k, {})
        expected_type = prop_def.get("type")

        if expected_type == "object" and isinstance(v, str):
            try:
                coerced[k] = json.loads(v)
                print(f"  [COERCE] {k}: string → dict")
            except json.JSONDecodeError:
                coerced[k] = v
        elif expected_type == "array" and isinstance(v, str):
            try:
                coerced[k] = json.loads(v)
                print(f"  [COERCE] {k}: string → list")
            except json.JSONDecodeError:
                coerced[k] = v
        elif expected_type == "integer" and isinstance(v, str):
            try:
                coerced[k] = int(v)
            except ValueError:
                coerced[k] = v
        elif expected_type == "boolean" and isinstance(v, str):
            coerced[k] = v.lower() in ("true", "1", "yes")
        elif isinstance(v, str) and expected_type is None:
            stripped = v.strip()
            if (stripped.startswith("{") and stripped.endswith("}")) or \
               (stripped.startswith("[") and stripped.endswith("]")):
                try:
                    coerced[k] = json.loads(v)
                    kind = "dict" if isinstance(coerced[k], dict) else "list"
                    print(f"  [COERCE] {k}: string → {kind} (auto-detect)")
                except json.JSONDecodeError:
                    coerced[k] = v
            else:
                coerced[k] = v
        else:
            coerced[k] = v
            
    # Avoid SQLAlchemy DetachedInstanceError on Superset backend
    if "generate_preview" in properties:
        coerced["generate_preview"] = False
        print("  [COERCE] Forced generate_preview=False to avoid DB session errors")
            
    # Auto-fix common LLM hallucination in chart configs
    if "config" in coerced and isinstance(coerced["config"], dict):
        cfg = coerced["config"]
        
        # Un-nest double config hallucination
        if "config" in cfg and isinstance(cfg["config"], dict):
            inner_cfg = cfg.pop("config")
            for k, v in inner_cfg.items():
                if k not in cfg:
                    cfg[k] = v
            print("  [COERCE] un-nested config.config")
            
        if "chartType" in cfg:
            cfg["chart_type"] = cfg.pop("chartType")
            print("  [COERCE] config.chartType -> config.chart_type")
            
        if "filters" in cfg and isinstance(cfg["filters"], list):
            for f in cfg["filters"]:
                if isinstance(f, dict):
                    if "val" in f and "value" not in f:
                        f["value"] = f.pop("val")
                    opr = f.get("opr", "").upper()
                    if opr in ("IS NOT NULL", "NOT NULL"):
                        f["opr"] = "!="
                        if "value" not in f: f["value"] = ""
                    elif opr == "IS NULL":
                        f["opr"] = "="
                        if "value" not in f: f["value"] = ""
                    if "value" not in f:
                        f["value"] = ""
            
        c_type = cfg.get("chart_type")
        
        def to_col_ref(val, is_metric=False):
            if isinstance(val, str): val = {"name": val}
            if isinstance(val, dict):
                if "column" in val:
                    val["name"] = val.pop("column")
                if "name" not in val: 
                    val["name"] = "count" if is_metric else val.get("label", "unknown")
                if is_metric and val.get("name") == "count":
                    val["saved_metric"] = True
                    val.pop("aggregate", None)
            return val

        def to_col_ref_list(val, is_metric=False):
            if isinstance(val, str): return [to_col_ref(val, is_metric)]
            if isinstance(val, list): return [to_col_ref(v, is_metric) for v in val]
            return val
        
        if c_type in ("pie", "big_number"):
            if "metrics" in cfg:
                metric_val = cfg.pop("metrics")
                cfg["metric"] = metric_val[0] if isinstance(metric_val, list) and metric_val else metric_val
            if "metric" in cfg:
                cfg["metric"] = to_col_ref(cfg["metric"], is_metric=True)
                    
            if c_type == "pie":
                if "groupby" in cfg:
                    gb = cfg["groupby"]
                    cfg["groupby"] = to_col_ref(gb[0] if isinstance(gb, list) and gb else gb, is_metric=False)
                    
        elif c_type == "xy":
            if "x_axis" in cfg: cfg["x"] = cfg.pop("x_axis")
            if "y_axis" in cfg: cfg["y"] = cfg.pop("y_axis")
            
            if "metric" in cfg and "y" not in cfg: cfg["y"] = [cfg.pop("metric")]
            elif "metrics" in cfg and "y" not in cfg: cfg["y"] = cfg.pop("metrics")
            cfg.pop("metric", None)
            cfg.pop("metrics", None)
            
            if "x" in cfg:
                if isinstance(cfg["x"], dict) and "name" not in cfg["x"]:
                    if "groupby" in cfg and isinstance(cfg["groupby"], list) and cfg["groupby"]:
                        gb_val = cfg["groupby"][0]
                        cfg["x"]["name"] = gb_val if isinstance(gb_val, str) else gb_val.get("name")
                        cfg["groupby"] = cfg["groupby"][1:]
                cfg["x"] = to_col_ref(cfg["x"], is_metric=False)
            if "y" in cfg: cfg["y"] = to_col_ref_list(cfg["y"], is_metric=True)
            if "groupby" in cfg: cfg["groupby"] = to_col_ref_list(cfg["groupby"], is_metric=False)
            
        elif c_type == "table":
            if "columns" in cfg: cfg["columns"] = to_col_ref_list(cfg["columns"], is_metric=False)
            if "groupby" in cfg: cfg["groupby"] = to_col_ref_list(cfg["groupby"], is_metric=False)
            if "metrics" in cfg: cfg["metrics"] = to_col_ref_list(cfg["metrics"], is_metric=True)
            cfg.pop("metric", None)
            if cfg.get("query_mode") not in ("aggregate", "raw"): 
                cfg["query_mode"] = "aggregate" if cfg.get("groupby") else "raw"
            
        elif c_type == "pivot_table":
            if "groupby" in cfg and "rows" not in cfg:
                cfg["rows"] = cfg.pop("groupby")
            if "rows" in cfg: cfg["rows"] = to_col_ref_list(cfg["rows"], is_metric=False)
            if "columns" in cfg: cfg["columns"] = to_col_ref_list(cfg["columns"], is_metric=False)
            if "metrics" in cfg: cfg["metrics"] = to_col_ref_list(cfg["metrics"], is_metric=True)
            cfg.pop("metric", None)
            
    return coerced


# ---------------------------------------------------------------------------
# Dynamic Tool Factory — with description trimming (Strategy 3)
# ---------------------------------------------------------------------------

_data_cache: dict[str, str] = {}  # key: "tool_name:args_hash" -> text response

def trim_description(desc: str, max_len: int = MAX_TOOL_DESC_LEN) -> str:
    """Cắt mô tả tool để tiết kiệm token context."""
    if not desc or len(desc) <= max_len:
        return desc
    # Cắt tại câu cuối cùng trước max_len
    truncated = desc[:max_len]
    last_period = truncated.rfind(". ")
    if last_period > max_len // 2:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "..."


def make_mcp_tool(tool_def: dict) -> BaseTool:
    tool_name = tool_def["name"]
    raw_desc = tool_def.get("description", tool_name).strip()
    tool_description = trim_description(raw_desc)  # Strategy 3: trim
    input_schema = tool_def.get("inputSchema", {})

    has_wrapper, properties, required_fields = analyze_schema(input_schema)
    is_list_tool = tool_name.startswith("list_")

    filtered_props = {
        k: v for k, v in properties.items()
        if not (is_list_tool and k in ("page", "page_size"))
    }
    args_model = build_pydantic_model(tool_name, filtered_props, required_fields)
    all_properties = properties

    _tool_has_wrapper = has_wrapper
    _tool_is_list = is_list_tool
    _tool_properties = all_properties

    class DynamicMcpTool(BaseTool):
        name: str = tool_name
        description: str = (
            tool_description + " Auto-paginated."
            if is_list_tool else tool_description
        )
        args_schema: Type[BaseModel] = args_model

        async def _arun(self, **kwargs: Any) -> str:
            clean_args = coerce_args(kwargs, _tool_properties)
            print(f"\n>>> [{self.name}] args={clean_args}")

            # Strategy 4: Cache lookups cho read-only tools
            cacheable_tools = {
                "list_datasets", "list_databases", "list_charts", "list_dashboards", 
                "get_dataset_info", "get_database_info", "get_chart_info", 
                "get_dashboard_info", "get_schema", "get_chart_type_schema"
            }
            is_cacheable = self.name in cacheable_tools
            cache_key = None
            
            if is_cacheable:
                # Remove cache-control arguments from key so they share the same cache entry
                key_args = {}
                for k, v in clean_args.items():
                    if k in ("force_refresh", "refresh_metadata", "use_cache", "page", "page_size"):
                        continue
                    # Skip falsy values (False, None, empty string/list/dict) as they are usually defaults
                    if not v:
                        continue
                    # Skip common defaults explicitly
                    if k == "order_direction" and v == "desc":
                        continue
                    key_args[k] = v
                
                args_str = json.dumps(sorted(key_args.items()), default=str)
                cache_key = f"{self.name}:{args_str}"
                
                # If LLM specifically asks for a refresh, bypass the read cache
                bypass_cache = clean_args.get("force_refresh") is True or clean_args.get("refresh_metadata") is True
                
                if not bypass_cache and cache_key in _data_cache:
                    cached_text = _data_cache[cache_key]
                    print(f"<<< [{self.name}] (CACHED) {cached_text[:300]}")
                    return cached_text

            if _tool_is_list:
                text = await fetch_all_pages(self.name, _tool_has_wrapper, clean_args)
            else:
                arguments = {"request": clean_args} if _tool_has_wrapper else clean_args
                result, notifications = await call_mcp("tools/call", {
                    "name": "call_tool",
                    "arguments": {"name": self.name, "arguments": arguments}
                })
                text = extract_text(result)
                if notifications:
                    text += f"\n\nSystem Notifications/Errors:\n" + "\n".join(notifications)
                
            if is_cacheable and text and not text.startswith("Tool '"):
                _data_cache[cache_key] = text

            print(f"<<< [{self.name}] {text[:300]}")
            return text or f"Tool '{self.name}' returned empty result."

        def _run(self, **kwargs: Any) -> str:
            return asyncio.run(self._arun(**kwargs))

    return DynamicMcpTool()


# ---------------------------------------------------------------------------
# Tool Discovery & Categorization — Strategy 2: Tool Filtering
# ---------------------------------------------------------------------------

# Phân nhóm tool theo intent category
TOOL_CATEGORIES = {
    "browse": {
        "tools": ["list_datasets", "list_charts", "list_dashboards", "list_databases",
                   "get_dataset_info", "get_chart_info", "get_dashboard_info", "get_database_info"],
        "search_queries": ["dataset", "chart", "dashboard", "database"],
    },
    "chart": {
        "tools": ["list_datasets", "get_dataset_info", "get_chart_type_schema",
                   "generate_chart", "generate_explore_link", "update_chart",
                   "get_chart_data", "get_chart_preview", "get_chart_sql", "update_chart_preview"],
        "search_queries": ["dataset", "chart", "schema"],
    },
    "dashboard": {
        "tools": ["list_charts", "list_dashboards", "get_dashboard_info",
                   "generate_dashboard", "add_chart_to_existing_dashboard",
                   "generate_chart", "list_datasets", "get_dataset_info", "get_chart_type_schema"],
        "search_queries": ["dashboard", "chart", "dataset", "schema"],
    },
    "sql": {
        "tools": ["list_databases", "get_database_info", "list_datasets", "get_dataset_info",
                   "execute_sql", "save_sql_query", "open_sql_lab_with_context", "create_virtual_dataset"],
        "search_queries": ["sql", "database", "query", "dataset"],
    },
    "system": {
        "tools": ["health_check", "get_instance_info", "get_schema", "generate_bug_report"],
        "search_queries": ["schema", "metric"],
    },
}

# Cache tool definitions globally
_all_tool_defs: dict[str, dict] = {}  # name -> tool_def
_tool_cache: dict[str, BaseTool] = {}  # name -> BaseTool instance


async def discover_all_tools() -> None:
    """Discover tất cả tools một lần và cache lại."""
    global _all_tool_defs
    if _all_tool_defs:
        return

    print("[DISCOVERY] Scanning MCP tools...")
    search_queries = set()
    for cat in TOOL_CATEGORIES.values():
        search_queries.update(cat["search_queries"])

    for query in search_queries:
        result, _ = await call_mcp("tools/call", {
            "name": "search_tools",
            "arguments": {"query": query}
        })
        text = extract_text(result)
        if not text:
            continue
        try:
            tools_found = json.loads(text)
            if isinstance(tools_found, list):
                for t in tools_found:
                    name = t.get("name")
                    if name and name not in _all_tool_defs:
                        _all_tool_defs[name] = t
        except json.JSONDecodeError:
            pass

    print(f"[DISCOVERY] Cached {len(_all_tool_defs)} tool definitions: {list(_all_tool_defs.keys())}")


def get_tools_for_category(category: str) -> list[BaseTool]:
    """Lấy subset tools cho một category, dùng cache."""
    cat_config = TOOL_CATEGORIES.get(category, TOOL_CATEGORIES["browse"])
    needed_names = cat_config["tools"]

    tools = []
    for name in needed_names:
        if name in _tool_cache:
            tools.append(_tool_cache[name])
        elif name in _all_tool_defs:
            tool = make_mcp_tool(_all_tool_defs[name])
            _tool_cache[name] = tool
            tools.append(tool)

    return tools


# ---------------------------------------------------------------------------
# Strategy 1: Tiered Models — Router + Executor
# ---------------------------------------------------------------------------

# Router model: rẻ, nhanh — chỉ phân loại intent
_router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=100)

ROUTER_PROMPT = """Classify the user query into exactly ONE category. Reply with ONLY the category name.

Categories:
- browse: listing, viewing, searching datasets/charts/dashboards/databases
- chart: creating, updating, previewing charts or visualizations
- dashboard: creating dashboards, adding charts to dashboards
- sql: executing SQL, saving queries, SQL Lab
- system: health check, instance info, schema discovery, bug reports
- chat: general conversation, greetings, questions not about Superset data

Query: {query}
Category:"""


async def route_query(query: str) -> str:
    """Dùng model rẻ để phân loại intent."""
    response = await _router_llm.ainvoke([
        HumanMessage(content=ROUTER_PROMPT.format(query=query))
    ])
    category = response.content.strip().lower().replace('"', '').replace("'", "")
    # Normalize
    valid = set(TOOL_CATEGORIES.keys()) | {"chat"}
    if category not in valid:
        category = "browse"  # fallback
    print(f"[ROUTER] '{query[:50]}...' -> {category}")
    return category


# System prompts — compact, per-category
EXECUTOR_PROMPTS = {
    "browse": (
        "You are a Superset assistant. Use tools to list/view datasets, charts, dashboards, databases.\n"
        "Respond in the user's language. Never fabricate data."
    ),
    "chart": (
        "You are a Superset chart builder.\n"
        "MANDATORY: ALWAYS call get_dataset_info FIRST to learn the dataset schema (columns, types, metrics) before any chart operation.\n"
        "Workflow: get_dataset_info → get_chart_type_schema(include_examples=true) → generate_chart.\n"
        "ColumnRef: {\"name\":\"col\",\"aggregate\":\"SUM\"} or {\"name\":\"metric\",\"saved_metric\":true}.\n"
        "CRITICAL CHART SCHEMA RULES:\n"
        "1. Allowed chart_types: 'xy', 'table', 'pie', 'pivot_table', 'mixed_timeseries', 'handlebars', 'big_number'.\n"
        "2. For bar or line charts, use chart_type='xy' and set kind='bar' or kind='line' inside config. NEVER use 'bar'/'line' as chart_type!\n"
        "3. For xy charts, ALWAYS use 'x' (dict) and 'y' (list of dicts) for axes. NEVER use 'x_axis' or 'y_axis'.\n"
        "4. 'pie' and 'big_number' use 'metric' (singular, dict), NOT 'metrics' (plural).\n"
        "5. 'table' does not use 'metric'/'metrics'. Use 'columns', 'groupby', 'query_mode'.\n"
        "6. 'chart_name' MUST be TOP-LEVEL, outside of the 'config' dict.\n"
        "7. Use snake_case for config keys ('chart_type', not 'chartType').\n"
        "Quick xy: {\"chart_type\":\"xy\",\"kind\":\"line\",\"x\":{\"name\":\"date\"},\"y\":[{\"name\":\"val\",\"aggregate\":\"SUM\"}]}\n"
        "Respond in the user's language."
    ),
    "dashboard": (
        "You are a Superset dashboard builder.\n"
        "MANDATORY: ALWAYS call get_dataset_info FIRST to learn the dataset schema (columns, types, metrics) before creating charts.\n"
        "Workflow: get_dataset_info → generate_chart(save_chart=true) for each chart → generate_dashboard(chart_ids=[...]).\n"
        "CRITICAL CHART SCHEMA RULES:\n"
        "1. Allowed chart_types: 'xy', 'table', 'pie', 'pivot_table', 'mixed_timeseries', 'handlebars', 'big_number'.\n"
        "2. For bar or line charts, use chart_type='xy' and set kind='bar' or kind='line' inside config. NEVER use 'bar'/'line' as chart_type!\n"
        "3. For xy charts, ALWAYS use 'x' (dict) and 'y' (list of dicts) for axes. NEVER use 'x_axis' or 'y_axis'.\n"
        "4. 'pie' and 'big_number' use 'metric' (singular, dict), NOT 'metrics' (plural).\n"
        "5. 'table' does not use 'metric'/'metrics'. Use 'columns', 'groupby', 'query_mode'.\n"
        "6. 'chart_name' MUST be TOP-LEVEL, outside of the 'config' dict.\n"
        "7. Use snake_case for config keys ('chart_type', not 'chartType').\n"
        "Call get_chart_type_schema before generate_chart if unsure about config format.\n"
        "Respond in the user's language."
    ),
    "sql": (
        "You are a Superset SQL assistant.\n"
        "MANDATORY: ALWAYS call get_dataset_info FIRST to learn the dataset schema (columns, types) before writing or executing SQL.\n"
        "Use execute_sql to run queries, save_sql_query to save, create_virtual_dataset to make chartable datasets.\n"
        "Respond in the user's language."
    ),
    "system": (
        "You are a Superset system assistant. Use health_check, get_instance_info, get_schema as needed.\n"
        "Respond in the user's language."
    ),
}


async def handle_chat(query: str) -> str:
    """Xử lý chat đơn giản — KHÔNG dùng tool, tiết kiệm tối đa."""
    response = await _router_llm.ainvoke([
        SystemMessage(content="You are a friendly Superset assistant. Answer briefly. Respond in the user's language."),
        HumanMessage(content=query),
    ])
    return response.content


# ---------------------------------------------------------------------------
# Agent Cache — one agent per category (Strategy 2)
# ---------------------------------------------------------------------------

_agents: dict[str, Any] = {}  # category -> agent


async def get_or_create_agent(category: str):
    """Lấy hoặc tạo agent cho một category."""
    if category in _agents:
        return _agents[category]

    await discover_all_tools()

    tools = get_tools_for_category(category)
    if not tools:
        raise RuntimeError(f"No tools found for category '{category}'")

    prompt = EXECUTOR_PROMPTS.get(category, EXECUTOR_PROMPTS["browse"])

    # Executor model — có thể dùng model xịn hơn ở đây
    executor_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    agent = create_react_agent(model=executor_llm, tools=tools, prompt=prompt)
    _agents[category] = agent

    tool_names = [t.name for t in tools]
    print(f"[AGENT] Created '{category}' agent with {len(tools)} tools: {tool_names}")
    return agent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def init_agent():
    """Pre-warm: discover tools and create browse agent."""
    await discover_all_tools()
    await get_or_create_agent("browse")
    
    print("\n[AGENT] Pre-warming cache for list tools...")
    tools_to_warm = ["list_datasets", "list_charts", "list_dashboards", "list_databases"]
    for t_name in tools_to_warm:
        tool = _tool_cache.get(t_name)
        if tool:
            print(f"  -> Warming up {t_name}...")
            # Run without arguments to populate cache
            await tool._arun()
            
    print("\n[AGENT] Initialized and ready.\n")


async def ask_agent(query: str) -> str:
    try:
        # Step 1: Route (cheap model, ~20 tokens output)
        category = await route_query(query)

        # Step 2: Chat shortcut — no tools needed
        if category == "chat":
            print(f"[AGENT] Chat mode (no tools)")
            return await handle_chat(query)

        # Step 3: Get specialized agent (cached, filtered tools)
        agent = await get_or_create_agent(category)
        tools = get_tools_for_category(category)

        print(f"[AGENT] Executing with {len(tools)} tools (category={category})")

        # Step 4: Execute
        result = await agent.ainvoke({"messages": [("human", query)]})
        response = result["messages"][-1].content
        if isinstance(response, list):
            response = "\n".join([
                c.get("text", "") for c in response
                if isinstance(c, dict) and c.get("type") == "text"
            ])
        print(f"[AGENT] Done.")
        return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Lỗi hệ thống: {str(e)}"

import json
async def stream_agent(query: str):
    """Gửi từng luồng sự kiện (tools + kết quả) cho UI dưới dạng NDJSON."""
    try:
        category = await route_query(query)
        yield json.dumps({"type": "log", "message": f"🤖 Phân loại yêu cầu: {category}"}) + "\n"
        
        if category == "chat":
            yield json.dumps({"type": "log", "message": f"💬 Trả lời trực tiếp..."}) + "\n"
            response = await handle_chat(query)
            yield json.dumps({"type": "message", "content": response}) + "\n"
            return
            
        agent = await get_or_create_agent(category)
        tools = get_tools_for_category(category)
        yield json.dumps({"type": "log", "message": f"⚙️ Đã nạp {len(tools)} công cụ (category: {category})"}) + "\n"
        
        final_response = ""
        async for chunk in agent.astream({"messages": [("human", query)]}):
            if "agent" in chunk:
                msg = chunk["agent"]["messages"][0]
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield json.dumps({"type": "log", "message": f"⚡ Đang chạy: {tc['name']}..."}) + "\n"
                elif msg.content:
                    if isinstance(msg.content, list):
                        final_response = "\n".join([c.get("text", "") for c in msg.content if isinstance(c, dict) and c.get("type") == "text"])
                    else:
                        final_response = str(msg.content)
            elif "tools" in chunk:
                for msg in chunk["tools"]["messages"]:
                    yield json.dumps({"type": "log", "message": f"✅ Hoàn tất công cụ: {msg.name}"}) + "\n"
                    
        yield json.dumps({"type": "message", "content": final_response}) + "\n"
    except Exception as e:
        import traceback
        traceback.print_exc()
        yield json.dumps({"type": "log", "message": f"❌ Lỗi: {str(e)}"}) + "\n"
        yield json.dumps({"type": "message", "content": f"Đã có lỗi hệ thống xảy ra: {str(e)}"}) + "\n"


if __name__ == "__main__":
    async def main():
        await init_agent()

        # Test router
        for q in [
            "Xin chào!",
            "Tôi có những dataset nào?",
            "Vẽ chart big_number cho dataset 27",
            "Tạo dashboard từ các chart",
            "Chạy SQL SELECT * FROM users",
        ]:
            cat = await route_query(q)
            print(f"  -> {cat}\n")

        # Test full flow
        answer = await ask_agent("Tôi đang có những bộ dataset nào?")
        print(f"\n{'='*60}\nKẾT QUẢ:\n{answer}")

    asyncio.run(main())