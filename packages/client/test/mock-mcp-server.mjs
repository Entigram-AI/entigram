import { createInterface } from 'node:readline';

const readline = createInterface({ input: process.stdin });

function reply(id, result) {
  process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id, result })}\n`);
}

readline.on('line', (line) => {
  const message = JSON.parse(line);
  if (message.method === 'initialize') {
    reply(message.id, {
      protocolVersion: message.params.protocolVersion,
      capabilities: { tools: {} },
      serverInfo: { name: 'mock-entigram', version: '0.1.0' },
    });
    return;
  }
  if (message.method !== 'tools/call') return;

  const { name, arguments: args } = message.params;
  let envelope;
  if (name === 'etg_get_capabilities') {
    envelope = { ok: true, capabilities: { tools: ['etg_get_capabilities'] } };
  } else if (name === 'etg_get_impact') {
    envelope = { ok: true, file_path: args.file_path, expectations: [] };
  } else if (name === 'etg_propose_alignment') {
    envelope = {
      ok: false,
      error: { code: 'UNKNOWN_CONCEPT', message: 'Error: unknown concept', details: 'fixture' },
    };
  } else {
    envelope = { ok: false, error: { code: 'UNKNOWN_TOOL', message: 'fixture' } };
  }
  reply(message.id, { content: [{ type: 'text', text: JSON.stringify(envelope) }] });
});
