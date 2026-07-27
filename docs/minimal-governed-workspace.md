# Minimal Governed Workspace

This example shows the smallest useful Entigram workflow for a local project.

## 1. Initialize

```bash
etg init --dir supplier-demo
cd supplier-demo
```

If `etg` is not on `PATH` while developing from source, run the module form from
the Entigram repository environment:

```bash
python3 -m entigram.cli_runner.etg_cli init --dir supplier-demo
```

## 2. Define The Closed-World Schema

Create `schema.lds`:

```text
ENTITY: Supplier
ATTRIBUTES:
  - .id (UUID)
  - name (String)
  - tax_id (String)

ENTITY: PurchaseOrder
ATTRIBUTES:
  - .id (UUID)
  - po_number (String)
  - total_amount (Float)

RELATIONSHIPS:
- Supplier (1) [MAY] --- [MUST] (MANY) PurchaseOrder
```

## 3. Hydrate The Agent

```bash
hydrate
```

The hydration summary tells the agent which schema, policy, Warden state,
delivery snapshot, and next broker commands govern the session.

## 4. Inspect A Planned Change

```bash
etg broker preflight --file schema.lds
etg broker impact --file schema.lds
```

Schema files are high-risk governed files. An agent should understand that
impact before editing.

## 5. Serve MCP Locally

```bash
etg serve
```

An MCP client can now call:

- `etg_get_schemas`
- `etg_get_impact`
- `etg_propose_alignment`
- `etg_log_conflict`

Unknown entities or attributes are rejected before they enter the ledger.

## 6. Handoff

Before returning work to a human reviewer:

```bash
etg broker handoff
etg broker status
```

The handoff is complete only when status reports:

```text
Delivery status: current
```

