# @entigram/client

Typed, dependency-free Node client for Entigram's local MCP governance server.
The client starts `etg serve` over a shell-free stdio transport, completes the
MCP initialize handshake, and exposes the current Entigram tool contract. It
does not duplicate policy evaluation or write `.etg/state.db` directly.

## Install

```sh
npm install @entigram/client
```

The `etg` command must already be installed and available on `PATH`:

```sh
pipx install entigram-ai
```

## Example

```js
import { EntigramClient } from '@entigram/client';

const entigram = new EntigramClient({ cwd: process.cwd() });

try {
  const capabilities = await entigram.getCapabilities();
  if (capabilities.ok === false) {
    console.error(capabilities.error.code);
    process.exitCode = 1;
  } else {
    const impact = await entigram.getImpact('src/order-service.ts');
    console.log(impact);
  }
} finally {
  await entigram.close();
}
```

Tool-level denials are returned as Entigram's stable `{ok: false, error:
{code, message, details}}` envelope. Transport failures, invalid MCP messages,
and timeouts throw `EntigramClientError`. Branch on `error.code`, not message
text.

The client maps the current tools as follows:

| Method | MCP tool |
| --- | --- |
| `getCapabilities()` | `etg_get_capabilities` |
| `getWorkspaceContext()` | `etg_get_workspace_context` |
| `getSchemas()` | `etg_get_schemas` |
| `getImpact(filePath)` | `etg_get_impact` |
| `getAssessmentCapabilities()` | `etg_get_assessment_capabilities` |
| `assess(payload)` | `etg_assess` |
| `proposeAlignment(payload)` | `etg_propose_alignment` |
| `logConflict(payload)` | `etg_log_conflict` |

`payload` may be a JSON object or an already encoded JSON string. The package
uses the existing MCP contract and should be versioned alongside that contract;
it is not a replacement for the Entigram Python runtime.
