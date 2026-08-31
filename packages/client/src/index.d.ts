export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject { [key: string]: JsonValue; }

export interface EntigramErrorEnvelope {
  code: string;
  message: string;
  details?: JsonValue;
}

export interface EntigramResponse<T extends JsonValue = JsonObject> extends JsonObject {
  ok?: boolean;
  error?: EntigramErrorEnvelope;
  data?: T;
}

export interface EntigramClientOptions {
  command?: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string | undefined>;
  protocolVersion?: string;
  timeoutMs?: number;
}

export class EntigramClientError extends Error {
  readonly code: string;
  constructor(message: string, options?: { code?: string; cause?: unknown });
}

export class EntigramClient {
  constructor(options?: EntigramClientOptions);
  readonly command: string;
  readonly args: string[];
  readonly cwd?: string;
  readonly protocolVersion: string;
  readonly timeoutMs: number;
  readonly serverInfo: JsonObject | null;
  readonly serverCapabilities: JsonObject | null;
  connect(): Promise<this>;
  callTool<T extends JsonValue = JsonObject>(name: string, args?: JsonObject): Promise<T>;
  getCapabilities(): Promise<EntigramResponse>;
  getWorkspaceContext(): Promise<EntigramResponse>;
  getSchemas(): Promise<EntigramResponse>;
  getImpact(filePath: string): Promise<EntigramResponse>;
  getAssessmentCapabilities(): Promise<EntigramResponse>;
  assess(payload: JsonObject | string): Promise<EntigramResponse>;
  proposeAlignment(payload: JsonObject | string): Promise<EntigramResponse>;
  logConflict(payload: JsonObject | string): Promise<EntigramResponse>;
  close(): Promise<void>;
  dispose(): Promise<void>;
}
