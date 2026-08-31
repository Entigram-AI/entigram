import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { EntigramClient, EntigramClientError } from '../src/index.js';

const fixture = resolve(dirname(fileURLToPath(import.meta.url)), 'mock-mcp-server.mjs');

function client() {
  return new EntigramClient({
    command: process.execPath,
    args: [fixture],
    spawn,
  });
}

test('completes the MCP handshake and calls typed convenience methods', async () => {
  const entigram = client();
  try {
    await entigram.connect();
    assert.deepEqual(entigram.serverInfo, { name: 'mock-entigram', version: '0.1.0' });
    assert.deepEqual(await entigram.getCapabilities(), {
      ok: true,
      capabilities: { tools: ['etg_get_capabilities'] },
    });
    assert.deepEqual(await entigram.getImpact('src/orders.ts'), {
      ok: true,
      file_path: 'src/orders.ts',
      expectations: [],
    });
  } finally {
    await entigram.close();
  }
});

test('returns Entigram tool denials without hiding the stable error code', async () => {
  const entigram = client();
  try {
    const response = await entigram.proposeAlignment({ source_concept: 'Ghost.id' });
    assert.equal(response.ok, false);
    assert.equal(response.error.code, 'UNKNOWN_CONCEPT');
  } finally {
    await entigram.close();
  }
});

test('rejects invalid payloads before sending a tool request', async () => {
  const entigram = client();
  assert.throws(
    () => entigram.assess(['not', 'an', 'object']),
    (error) => error instanceof TypeError,
  );
  await entigram.close();
});

test('exposes transport failures as EntigramClientError', async () => {
  const entigram = new EntigramClient({
    command: process.execPath,
    args: ['-e', 'process.exit(7)'],
    timeoutMs: 500,
  });
  await assert.rejects(
    () => entigram.connect(),
    (error) => error instanceof EntigramClientError && error.code === 'CLIENT_PROCESS_EXITED',
  );
  await entigram.close();
});
