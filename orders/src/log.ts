// logfmt: time=... level=info service=orders msg="..." key=value
export type Level = "debug" | "info" | "warn" | "error";
export type Fields = Record<string, unknown>;

export interface Logger {
  debug(msg: string, fields?: Fields): void;
  info(msg: string, fields?: Fields): void;
  warn(msg: string, fields?: Fields): void;
  error(msg: string, fields?: Fields): void;
}

function value(v: unknown): string {
  if (v instanceof Error) v = v.message;
  if (v === null || v === undefined) return "";
  const s = typeof v === "string" ? v : String(v);
  if (s === "" || /[\s"=\\]/.test(s)) {
    return JSON.stringify(s);
  }
  return s;
}

export function logfmt(fields: Fields): string {
  return Object.entries(fields)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${k}=${value(v)}`)
    .join(" ");
}

const ORDER: Level[] = ["debug", "info", "warn", "error"];

export function createLogger(
  service = "orders",
  min: Level = "info",
  write: (line: string) => void = (l) => process.stdout.write(l + "\n"),
): Logger {
  const at = (level: Level) => (msg: string, fields: Fields = {}) => {
    if (ORDER.indexOf(level) < ORDER.indexOf(min)) return;
    write(logfmt({
      time: new Date().toISOString(),
      level,
      service,
      msg,
      ...fields,
    }));
  };
  return {
    debug: at("debug"),
    info: at("info"),
    warn: at("warn"),
    error: at("error"),
  };
}
