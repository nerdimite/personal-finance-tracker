import { allowedDashboardHost } from "@/lib/host";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  if (allowedDashboardHost(request.headers.get("host"))) {
    return NextResponse.next();
  }
  return new NextResponse("Forbidden", {
    status: 403,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "private, no-store",
    },
  });
}
