/** The port the browser uses, i.e. the host side of the compose mapping. */
export function dashboardPort(): string {
  return process.env.DASHBOARD_PORT?.trim() || "4100";
}

export function allowedDashboardHost(host: string | null | undefined, port: string = dashboardPort()): boolean {
  if (!host) return false;
  const value = host.trim().toLowerCase();
  return value === `localhost:${port}` || value === `127.0.0.1:${port}`;
}
