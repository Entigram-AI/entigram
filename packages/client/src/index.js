import { spawn as defaultSpawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const DEFAULT_PROTOCOL_VERSION = '2025-06-18';
const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * Error raised when the MCP transport cannot complete a request.
 * Entigram tool-level denials are returned as normal `{ok: false}` envelopes
 * so callers can branch on `error.code` without catching exceptions.
 */
export class EntigramClientError extends Error {
  constructor(message, { code = 'CLIENT_ERROR', cause } = {}) {
    super(message, { cause });
    this.name = 'EntigramClientError';
    this.code = code;
  }
}

/**
 * A small MCP stdio client for the local `etg serve` process.
 *
 * The client intentionally delegates governance decisions to Entigram's
 * canonical runtime. It does not implement policy evaluation or write the
 * workspace ledger itself.
 */
export class EntigramClient {
  constructor({
    command = 'etg',
    args = ['serve'],
    cwd,
    env,
    protocolVersion = DEFAULT_PROTOCOL_VERSION,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    spawn = defaultSpawn,
  } = {}) {
    if (typeof command !== 'string' || command.length === 0) {
      throw new TypeError('command must be a non-empty string');
    }
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string')) {
      throw new TypeError('args must be an array of strings');
    }
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new TypeError('timeoutMs must be a positive number');
    }

    this.command = command;
    this.args = [...args];
    this.cwd = cwd;
    this.env = env;
    this.protocolVersion = protocolVersion;
    this.timeoutMs = timeoutMs;
    this.spawn = spawn;
    this.child = null;
    this.readline = null;
    this.pending = new Map();
    this.nextRequestId = 1;
    this.connecting = null;
    this.connected = false;
    this.closed = false;
    this.serverInfo = null;
    this.serverCapabilities = null;
    this.stderr = '';
  }

  /** Start the local MCP server and complete the MCP initialize handshake. */
  async connect() {
    if (this.connected) return this;
    if (this.connecting) return this.connecting;

    this.closed = false;
    this.connecting = this.#start();
    try {
      await this.connecting;
      return this;
    } finally {
      this.connecting = null;
    }
  }

  async #start() {
    if (this.child) {
      throw new EntigramClientError('The client process is already started', {
        code: 'CLIENT_ALREADY_STARTED',
      });
    }

    let child;
    try {
      child = this.spawn(this.command, this.args, {
        cwd: this.cwd,
        env: this.env ? { ...process.env, ...this.env } : process.env,
        shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      throw new EntigramClientError(`Could not start ${this.command}`, {
        code: 'CLIENT_SPAWN_FAILED',
        cause: error,
      });
    }

    this.child = child;
    this.readline = createInterface({ input: child.stdout });
    this.readline.on('line', (line) => this.#handleLine(line));
    child.stderr?.on('data', (chunk) => {
      this.stderr += String(chunk);
    });
    child.on('error', (error) => {
      this.#failPending(new EntigramClientError(`Entigram server failed: ${error.message}`, {
        code: 'CLIENT_PROCESS_ERROR',
        cause: error,
      }));
    });
    child.on('exit', (code, signal) => {
      this.connected = false;
      if (!this.closed) {
        const detail = signal ? `signal ${signal}` : `code ${code}`;
        this.#failPending(new EntigramClientError(`Entigram server exited with ${detail}`, {
          code: 'CLIENT_PROCESS_EXITED',
        }));
      }
    });

    try {
      const initialized = await this.#request('initialize', {
        protocolVersion: this.protocolVersion,
        capabilities: {},
        clientInfo: { name: '@entigram/client', version: '0.1.0' },
      });
      this.serverInfo = initialized?.serverInfo ?? null;
      this.serverCapabilities = initialized?.capabilities ?? null;
      this.#notify('notifications/initialized', {});
      this.connected = true;
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  /**
   * Call an Entigram MCP tool and return its parsed stable JSON envelope.
   * Transport and protocol failures throw `EntigramClientError`; tool-level
   * denials are returned with `ok: false`.
   */
  async callTool(name, args = {}) {
    if (typeof name !== 'string' || name.length === 0) {
      throw new TypeError('name must be a non-empty string');
    }
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
      throw new TypeError('args must be an object');
    }
    await this.connect();
    const result = await this.#request('tools/call', { name, arguments: args });
    const text = result?.content?.find((item) => item?.type === 'text')?.text;
    if (typeof text !== 'string') {
      throw new EntigramClientError(`Tool ${name} returned no JSON text content`, {
        code: 'INVALID_TOOL_RESPONSE',
      });
    }
    try {
      return JSON.parse(text);
    } catch (error) {
      throw new EntigramClientError(`Tool ${name} returned invalid JSON`, {
        code: 'INVALID_TOOL_RESPONSE',
        cause: error,
      });
    }
  }

  getCapabilities() {
    return this.callTool('etg_get_capabilities');
  }

  getWorkspaceContext() {
    return this.callTool('etg_get_workspace_context');
  }

  getSchemas() {
    return this.callTool('etg_get_schemas');
  }

  getImpact(filePath) {
    if (typeof filePath !== 'string' || filePath.length === 0) {
      throw new TypeError('filePath must be a non-empty string');
    }
    return this.callTool('etg_get_impact', { file_path: filePath });
  }

  getAssessmentCapabilities() {
    return this.callTool('etg_get_assessment_capabilities');
  }

  assess(payload) {
    return this.callTool('etg_assess', { payload: encodePayload(payload) });
  }

  proposeAlignment(payload) {
    return this.callTool('etg_propose_alignment', { payload: encodePayload(payload) });
  }

  logConflict(payload) {
    return this.callTool('etg_log_conflict', { payload: encodePayload(payload) });
  }

  /** Stop the local server and reject any in-flight requests. */
  async close() {
    this.closed = true;
    this.connected = false;
    this.#failPending(new EntigramClientError('Entigram client closed', {
      code: 'CLIENT_CLOSED',
    }));
    this.readline?.close();
    this.readline = null;
    const child = this.child;
    this.child = null;
    if (!child || child.killed) return;
    child.kill();
  }

  dispose() {
    return this.close();
  }

  #notify(method, params) {
    if (!this.child?.stdin?.writable) return;
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  }

  #request(method, params) {
    if (!this.child?.stdin?.writable) {
      return Promise.reject(new EntigramClientError('Entigram server is not running', {
        code: 'CLIENT_NOT_CONNECTED',
      }));
    }
    const id = this.nextRequestId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new EntigramClientError(`Timed out waiting for MCP response to ${method}`, {
          code: 'CLIENT_TIMEOUT',
        }));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(new EntigramClientError(`Could not send MCP request ${method}`, {
          code: 'CLIENT_WRITE_FAILED',
          cause: error,
        }));
      }
    });
  }

  #handleLine(line) {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch (error) {
      this.#failPending(new EntigramClientError('Entigram server emitted invalid JSON', {
        code: 'INVALID_MCP_MESSAGE',
        cause: error,
      }));
      return;
    }
    if (!Object.hasOwn(message, 'id')) return;
    const request = this.pending.get(message.id);
    if (!request) return;
    this.pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) {
      request.reject(new EntigramClientError(message.error.message ?? 'MCP request failed', {
        code: message.error.code ?? 'MCP_ERROR',
      }));
    } else {
      request.resolve(message.result);
    }
  }

  #failPending(error) {
    for (const [id, request] of this.pending) {
      clearTimeout(request.timer);
      request.reject(error);
      this.pending.delete(id);
    }
  }
}

function encodePayload(payload) {
  if (typeof payload === 'string') return payload;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new TypeError('payload must be a JSON object or JSON string');
  }
  return JSON.stringify(payload);
}
