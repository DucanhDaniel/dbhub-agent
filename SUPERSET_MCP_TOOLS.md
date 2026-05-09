# Superset MCP Tools Reference

All tools use JSON-RPC over HTTP POST to `http://localhost:5008/mcp`.
Arguments are wrapped in `{"request": {...}}` unless noted otherwise.

---

## 1. Discovery & Schema Tools

### `health_check`
No parameters. Returns service status.

### `get_instance_info`
No parameters. Returns counts, activity metrics, database types.

### `get_schema`
Get metadata for a model type (select/filter/sort columns).
```json
{"request": {"model_type": "chart|dataset|dashboard|database"}}
```

### `get_chart_type_schema`
Get full JSON Schema + examples for a chart type. **No `request` wrapper.**
```json
{"chart_type": "xy|table|pie|pivot_table|mixed_timeseries|handlebars|big_number", "include_examples": true}
```

---

## 2. Listing Tools (all support pagination)

All `list_*` tools share common params: `page` (1-based), `page_size` (max 100), `search`, `filters`, `select_columns`, `order_column`, `order_direction`.

### `list_datasets`
```json
{"request": {"search": "account", "page": 1, "page_size": 10}}
```
Filter columns: `table_name`, `schema`, `database_name`
Sort columns: `id`, `table_name`, `schema`, `changed_on`, `created_on`

### `list_charts`
```json
{"request": {"search": "spend", "page": 1, "page_size": 10}}
```
Filter columns: `slice_name`, `viz_type`, `datasource_name`, `datasource_id`
Sort columns: `id`, `slice_name`, `viz_type`, `changed_on`, `created_on`

### `list_dashboards`
```json
{"request": {"search": "performance", "page": 1, "page_size": 10}}
```
Filter columns: `dashboard_title`, `published`, `favorite`
Sort columns: `id`, `dashboard_title`, `slug`, `published`, `changed_on`, `created_on`

### `list_databases`
```json
{"request": {"page": 1, "page_size": 10}}
```
Filter columns: `database_name`, `expose_in_sqllab`, `allow_file_upload`
Sort columns: `id`, `database_name`, `changed_on`, `created_on`

---

## 3. Detail Tools

### `get_dataset_info`
Returns columns, metrics, schema. Use ID or UUID.
```json
{"request": {"identifier": 27}}
```

### `get_chart_info`
Returns chart config, form_data. Use ID or UUID.
```json
{"request": {"identifier": 157}}
```
Optional: `form_data_key` for unsaved chart state.

### `get_dashboard_info`
Returns title, charts, layout. Use ID, UUID, or slug.
```json
{"request": {"identifier": 15}}
```
Optional: `permalink_key` for applied filter state.

### `get_database_info`
Returns backend type, capabilities. Use ID or UUID.
```json
{"request": {"identifier": 2}}
```

### `get_chart_sql`
Get rendered SQL for a chart without executing.
```json
{"request": {"identifier": 157}}
```

---

## 4. SQL Tools

### `execute_sql`
Execute SQL query against a database.
```json
{"request": {"database_id": 2, "sql": "SELECT * FROM table LIMIT 10", "timeout": 30}}
```
Optional: `schema`, `catalog`, `limit` (overrides SQL LIMIT), `template_params`, `dry_run`, `force_refresh`.

### `save_sql_query`
Save a SQL query to SQL Lab's Saved Queries.
```json
{"request": {"database_id": 2, "label": "My Query", "sql": "SELECT ..."}}
```
Optional: `schema`, `catalog`, `description`.

### `open_sql_lab_with_context`
Generate a SQL Lab URL with pre-populated context.
```json
{"request": {"database_connection_id": 2, "sql": "SELECT ...", "schema": "public"}}
```

---

## 5. Chart Creation & Update Tools

### ColumnRef (shared type)
All metrics/columns use this format:
```json
{"name": "column_name", "aggregate": "SUM|COUNT|AVG|MIN|MAX|COUNT_DISTINCT|STDDEV|VAR|MEDIAN|PERCENTILE"}
```
For saved metrics: `{"name": "metric_name", "saved_metric": true}`

### Filter (shared type)
```json
{"column": "col_name", "op": "=|>|<|>=|<=|!=|LIKE|ILIKE|IN|NOT IN", "value": "..."}
```

### `generate_chart`
Create and save a chart. `save_chart` defaults to `true`.
```json
{"request": {
  "dataset_id": 27,
  "chart_name": "My Chart",
  "save_chart": true,
  "config": { ... }
}}
```

### `generate_explore_link`
Generate an explore URL (preview, no save).
```json
{"request": {
  "dataset_id": 27,
  "config": { ... }
}}
```

### `update_chart`
Update an existing chart by ID/UUID.
```json
{"request": {
  "identifier": 157,
  "config": { ... },
  "chart_name": "New Name",
  "generate_preview": true
}}
```

### Chart Config Types

#### `xy` — Line/Bar/Area/Scatter
```json
{
  "chart_type": "xy",
  "kind": "line|bar|area|scatter",
  "y": [{"name": "spend", "aggregate": "SUM"}],
  "x": {"name": "date_start"},
  "group_by": [{"name": "account_name"}],
  "time_grain": "P1D|P1W|P1M|P1Y",
  "stacked": false,
  "orientation": "vertical|horizontal",
  "filters": [{"column": "...", "op": "=", "value": "..."}],
  "row_limit": 10000
}
```
Required: `y`. Optional: `x` (defaults to main_dttm_col).

#### `big_number` — Single Metric Display
```json
{
  "chart_type": "big_number",
  "metric": {"name": "spend", "aggregate": "SUM"},
  "subheader": "Total Spend",
  "y_axis_format": "$,.2f",
  "show_trendline": true,
  "temporal_column": "date_start",
  "time_grain": "P1D",
  "compare_lag": 7
}
```
Required: `chart_type`, `metric`.

#### `pie` — Pie/Donut Chart
```json
{
  "chart_type": "pie",
  "dimension": {"name": "account_name"},
  "metric": {"name": "spend", "aggregate": "SUM"},
  "donut": false,
  "show_labels": true,
  "label_type": "key_value_percent",
  "row_limit": 100
}
```
Required: `dimension`, `metric`.

#### `table` — Data Table
```json
{
  "chart_type": "table",
  "columns": [
    {"name": "account_name"},
    {"name": "spend", "aggregate": "SUM"},
    {"name": "purchases", "aggregate": "SUM"}
  ],
  "query_mode": "aggregate|raw",
  "row_limit": 1000
}
```
Required: `columns`.

#### `pivot_table` — Pivot Table
```json
{
  "chart_type": "pivot_table",
  "rows": [{"name": "account_name"}],
  "metrics": [{"name": "spend", "aggregate": "SUM"}],
  "columns": [{"name": "date_start"}],
  "aggregate_function": "Sum|Average|Count|...",
  "show_row_totals": true,
  "show_column_totals": true
}
```
Required: `rows`, `metrics`.

#### `mixed_timeseries` — Dual Axis Chart
```json
{
  "chart_type": "mixed_timeseries",
  "x": {"name": "date_start"},
  "y": [{"name": "spend", "aggregate": "SUM"}],
  "y_secondary": [{"name": "purchases", "aggregate": "SUM"}],
  "primary_kind": "line",
  "secondary_kind": "bar",
  "time_grain": "P1D"
}
```
Required: `x`, `y`, `y_secondary`.

#### `handlebars` — Custom HTML Template
```json
{
  "chart_type": "handlebars",
  "handlebars_template": "<ul>{{#each data}}<li>{{this.name}}: {{this.value}}</li>{{/each}}</ul>",
  "query_mode": "aggregate|raw",
  "groupby": [{"name": "account_name"}],
  "metrics": [{"name": "spend", "aggregate": "SUM"}],
  "row_limit": 1000
}
```
Required: `chart_type`, `handlebars_template`.

---

## 6. Dashboard Tools

### `generate_dashboard`
Create a new dashboard from chart IDs.
```json
{"request": {
  "chart_ids": [157, 158, 159],
  "dashboard_title": "My Dashboard",
  "published": true
}}
```
Required: `chart_ids`.

### `add_chart_to_existing_dashboard`
Add a chart to an existing dashboard.
```json
{"request": {
  "dashboard_id": 15,
  "chart_id": 160,
  "target_tab": null
}}
```
Required: `dashboard_id`, `chart_id`.

---

## 7. Dataset Creation

### `create_virtual_dataset`
Save a SQL query as a virtual dataset for charting.
```json
{"request": {
  "database_id": 2,
  "sql": "SELECT a.*, b.name FROM table_a a JOIN table_b b ON a.id = b.id",
  "dataset_name": "joined_data"
}}
```
Required: `database_id`, `sql`, `dataset_name`.

---

## 8. Utility Tools

### `search_tools`
Search available tools by natural language. **No `request` wrapper.**
```json
{"query": "create chart"}
```

### `generate_bug_report`
Generate a bug report for MCP issues.
```json
{"request": {"tool_name": "generate_chart", "error_message": "..."}}
```

---

## Common Patterns

### Workflow: Data → Chart → Dashboard
1. `list_datasets` → find dataset ID
2. `get_dataset_info` → see columns & metrics
3. `generate_chart` (save_chart=true) → get chart ID
4. `generate_dashboard` or `add_chart_to_existing_dashboard`

### Aggregation Functions
`SUM`, `COUNT`, `AVG`, `MIN`, `MAX`, `COUNT_DISTINCT`, `STDDEV`, `VAR`, `MEDIAN`, `PERCENTILE`

### Time Grains
`PT1S` (second), `PT1M` (minute), `PT1H` (hour), `P1D` (day), `P1W` (week), `P1M` (month), `P3M` (quarter), `P1Y` (year)

### Number Formats
`$,.2f` (currency), `,.0f` (integer), `.2%` (percentage), `SMART_NUMBER` (auto)
