// Shared HTTP client: timeouts, retries, circuit breaker, x-trace-id.
import { randomUUID } from "node:crypto";

export const TRACE_HEADER = "x-trace-id";
export const IDEMPOTENCY_HEADER = "idempotency-key";

const RETRY_STATUSES = new Set([502, 503, 504]);
const CONNECT_CODES = new Set([
  "ECONNREFUSED",
  "ECONNRESET",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "ENOTFOUND",
  "EAI_AGAIN",
]);

export interface BreakerOptions {
  failures?: number;
  open_ms?: number;
}

export interface ClientOptions {
  timeout_ms?: number;
  retries?: number;
  backoff_ms?: number;
  breaker?: BreakerOptions;
}

export interface CallOptions {
  traceId?: string;
  headers?: Record<string, string>;
}

export interface Response {
  status: number;
  headers: Headers;
  body: string;
  traceId: string;
  json<T = unknown>(): T;
}

export class CircuitOpenError extends Error {
  constructor(baseUrl: string) {
    super(`circuit open for ${baseUrl}`);
    this.name = "CircuitOpenError";
  }
}

export class ConnectError extends Error {
  constructor(url: string, cause: unknown) {
    super(`connect failed: ${url}`, { cause });
    this.name = "ConnectError";
  }
}

export class TimeoutError extends Error {
  constructor(url: string, ms: number) {
    super(`timeout after ${ms} ms: ${url}`);
    this.name = "TimeoutError";
  }
}

function isConnectError(err: unknown): boolean {
  let e: any = err;
  for (let i = 0; e && i < 3; i++) {
    if (CONNECT_CODES.has(e.code)) return true;
    e = e.cause;
  }
  return false;
}

const sleep = (ms: number) =>
  new Promise((r) => setTimeout(r, ms));

export class Client {
  readonly baseUrl: string;
  private timeoutMs: number;
  private retries: number;
  private backoffMs: number;
  private maxFailures: number;
  private openMs: number;
  private failures = 0;
  private openedAt: number | null = null;

  constructor(baseUrl: string, opts: ClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = opts.timeout_ms ?? 2000;
    this.retries = opts.retries ?? 2;
    this.backoffMs = opts.backoff_ms ?? 100;
    this.maxFailures = opts.breaker?.failures ?? 5;
    this.openMs = opts.breaker?.open_ms ?? 10000;
  }

  get breakerState(): "closed" | "open" | "half-open" {
    if (this.openedAt === null) return "closed";
    return Date.now() - this.openedAt < this.openMs
      ? "open"
      : "half-open";
  }

  get(path: string, opts: CallOptions = {}) {
    return this.request("GET", path, undefined, undefined, opts);
  }

  post(
    path: string,
    json: unknown,
    idempotencyKey?: string,
    opts: CallOptions = {},
  ) {
    return this.request("POST", path, json, idempotencyKey, opts);
  }

  private async request(
    method: string,
    path: string,
    json: unknown,
    idempotencyKey: string | undefined,
    opts: CallOptions,
  ): Promise<Response> {
    if (this.breakerState === "open") {
      throw new CircuitOpenError(this.baseUrl);
    }
    const traceId = opts.traceId || randomUUID();
    const headers: Record<string, string> = {
      ...opts.headers,
      [TRACE_HEADER]: traceId,
    };
    if (json !== undefined) headers["content-type"] = "application/json";
    if (idempotencyKey) headers[IDEMPOTENCY_HEADER] = idempotencyKey;
    const url = this.baseUrl + path;
    const body = json === undefined ? undefined : JSON.stringify(json);

    for (let attempt = 0; ; attempt++) {
      const last = attempt >= this.retries;
      let res: globalThis.Response;
      try {
        res = await fetch(url, {
          method,
          headers,
          body,
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (err: any) {
        if (err?.name === "TimeoutError" || err?.name === "AbortError") {
          // Not retried: the server may still be doing the work.
          this.onFailure();
          throw new TimeoutError(url, this.timeoutMs);
        }
        if (isConnectError(err) && !last) {
          await sleep(this.backoffMs * 2 ** attempt);
          continue;
        }
        this.onFailure();
        throw isConnectError(err) ? new ConnectError(url, err) : err;
      }
      if (RETRY_STATUSES.has(res.status) && !last) {
        await res.arrayBuffer().catch(() => undefined);
        await sleep(this.backoffMs * 2 ** attempt);
        continue;
      }
      let text: string;
      try {
        text = await res.text();
      } catch (err: any) {
        this.onFailure();
        if (err?.name === "TimeoutError" || err?.name === "AbortError") {
          throw new TimeoutError(url, this.timeoutMs);
        }
        throw err;
      }
      if (res.status >= 500) this.onFailure();
      else this.onSuccess();
      return {
        status: res.status,
        headers: res.headers,
        body: text,
        traceId,
        json: <T>() => JSON.parse(text) as T,
      };
    }
  }

  private onSuccess() {
    this.failures = 0;
    this.openedAt = null;
  }

  private onFailure() {
    this.failures++;
    if (this.breakerState === "half-open" || this.failures >= this.maxFailures) {
      this.openedAt = Date.now();
    }
  }
}

export function client(baseUrl: string, opts: ClientOptions = {}) {
  return new Client(baseUrl, opts);
}
