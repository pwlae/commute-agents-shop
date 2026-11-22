// JSON logs: one object per line on stdout.

export type Fields = Record<string, unknown>;
export type Logger = (level: string, msg: string, f?: Fields) => void;

export function jsonLogger(
  write: (line: string) => void = (l) => process.stdout.write(l + "\n"),
): Logger {
  return (level, msg, fields = {}) =>
    write(JSON.stringify({
      time: new Date().toISOString(),
      level,
      service: "gateway",
      msg,
      ...fields,
    }));
}
