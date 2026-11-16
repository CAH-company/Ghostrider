import { NextResponse } from "next/server";
import { COOKIE, authToken, passwordOk } from "@/lib/auth";

export async function POST(req: Request) {
  const { password } = await req.json().catch(() => ({ password: "" }));
  if (!passwordOk(String(password ?? ""))) {
    return NextResponse.json({ error: "Złe hasło" }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, await authToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 60,
  });
  return res;
}
