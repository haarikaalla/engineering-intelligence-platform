import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
const SAFE_SEGMENT = /^[A-Za-z0-9._\-]+$/;

/** Server-side proxy: keeps the backend API key out of the browser and avoids CORS. */
async function proxy(req: NextRequest, { params }: { params: { path: string[] } }) {
  const segments = params.path ?? [];
  if (segments.some((s) => !SAFE_SEGMENT.test(s) || s === "." || s === "..")) {
    return NextResponse.json({ detail: "Invalid path" }, { status: 400 });
  }
  const search = new URL(req.url).search;
  const target = `${BACKEND_URL}/api/${segments.join("/")}${search}`;
  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  if (process.env.EIP_API_KEY) headers.set("x-api-key", process.env.EIP_API_KEY);

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      cache: "no-store",
    });
    if (upstream.status === 204) return new NextResponse(null, { status: 204 });
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as DELETE };
