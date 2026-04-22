# Envelope contract

Every MCP tool in the suite returns a uniform shape:

```python
class Envelope[T]:
    status: Literal["ok", "error"]
    data: T | None
    warnings: list[str]
    metadata: dict[str, Any]   # tool_version, runtime_sec, etc.
    error: str | None
```

## Why a uniform shape

Predictability matters more than minimalism here. An LLM agent calls
forty different tools across three servers and needs a contract it can
rely on for routing logic ("did the call succeed?", "what's the
runtime?", "are there warnings I should propagate?"). The envelope
gives it that without per-tool special cases.

## Construction helpers

::: rf_mcp_common.envelope
    options:
      heading_level: 3
      members:
        - Envelope
        - ok
        - error
        - Timer
      show_source: true
      members_order: source
      show_root_heading: false
