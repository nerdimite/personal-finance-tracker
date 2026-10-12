export function databaseUrlFrom(raw: string, host?: string, port?: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("DASHBOARD_DATABASE_URL is not a valid URL");
  }
  if (url.protocol !== "postgresql:" && url.protocol !== "postgres:") {
    throw new Error("DASHBOARD_DATABASE_URL must be a PostgreSQL URL");
  }
  if (host) url.hostname = host;
  if (port) url.port = port;
  return url.toString();
}

export function safeDatabaseTarget(url: string): string {
  const parsed = new URL(url);
  const port = parsed.port || "5432";
  const name = parsed.pathname.replace(/^\//, "") || "?";
  return `${parsed.hostname}:${port}/${name}`;
}
