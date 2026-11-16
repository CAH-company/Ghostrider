import { NextResponse, type NextRequest } from "next/server";
import { COOKIE, authToken } from "@/lib/auth";

export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/login" || pathname === "/api/login") return NextResponse.next();

  const ok = req.cookies.get(COOKIE)?.value === (await authToken());
  if (ok) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.redirect(new URL("/login", req.url));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
