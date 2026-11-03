import { NextResponse, type NextRequest } from "next/server";

/**
 * /engine/* -> the shared demo engine, from the server side. The browser only ever talks to this origin; the
 * engine's address (ENGINE_UPSTREAM) and its access value (ENGINE_TOKEN) live in the deployment's environment,
 * never in the repo or the client bundle. Media streams through the same rewrite (range requests included).
 */
export function proxy(request: NextRequest) {
  const upstream = process.env.ENGINE_UPSTREAM?.trim().replace(/\/+$/, "");
  const access = process.env.ENGINE_TOKEN?.trim();
  if (!upstream || !access) {
    const detail = "The shared demo engine is not configured on this deployment.";
    return NextResponse.json({ error: detail, detail }, { status: 503 });
  }
  const path = request.nextUrl.pathname.replace(/^\/engine/, "") || "/";
  const target = new URL(`${upstream}${path}${request.nextUrl.search}`);

  const headers = new Headers(request.headers);
  headers.set("x-access-token", access);
  headers.delete("authorization");
  headers.delete("cookie");
  // The engine caps renders per visitor; the platform sets x-real-ip, and a client-sent x-client-ip is never trusted.
  const ip = request.headers.get("x-real-ip") ?? request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (ip) headers.set("x-client-ip", ip);
  else headers.delete("x-client-ip");

  return NextResponse.rewrite(target, { request: { headers } });
}

export const config = { matcher: "/engine/:path*" };
