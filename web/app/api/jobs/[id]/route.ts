import { NextResponse } from "next/server";
import { getJson, presignGet } from "@/lib/r2";

export async function GET(_: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+-[0-9a-f]{6}$/.test(id)) return NextResponse.json({ error: "bad id" }, { status: 400 });
  const [meta, status] = await Promise.all([getJson(`jobs/${id}/meta.json`), getJson(`jobs/${id}/status.json`)]);
  if (!meta) return NextResponse.json({ error: "not found" }, { status: 404 });
  const out: any = { ...meta, ...(status ?? { state: "queued", progress: 0 }) };
  if (out.state === "done") {
    out.video_url = await presignGet(`jobs/${id}/overlay.mp4`);
    out.download_url = await presignGet(`jobs/${id}/overlay.mp4`, `twinrun-${out.name_a}-${out.name_b}.mp4`);
  }
  return NextResponse.json(out, { headers: { "Cache-Control": "no-store" } });
}
