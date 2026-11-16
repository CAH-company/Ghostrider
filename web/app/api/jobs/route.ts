import { NextResponse } from "next/server";
import { ListObjectsV2Command, PutObjectCommand } from "@aws-sdk/client-s3";
import { randomBytes } from "node:crypto";
import { BUCKET, getJson, s3 } from "@/lib/r2";

const num = (v: unknown) => (v === null || v === undefined || v === "" ? null : Number(v));

const put = (key: string, body: unknown) =>
  s3().send(
    new PutObjectCommand({
      Bucket: BUCKET,
      Key: key,
      Body: JSON.stringify(body),
      ContentType: "application/json",
      CacheControl: "no-store",
    }),
  );

export async function POST(req: Request) {
  const b = await req.json();
  if (!String(b.a_key).startsWith("uploads/") || !String(b.b_key).startsWith("uploads/")) {
    return NextResponse.json({ error: "bad keys" }, { status: 400 });
  }
  const id = `${Date.now()}-${randomBytes(3).toString("hex")}`;
  const meta = {
    id,
    created: new Date().toISOString(),
    a_key: b.a_key,
    b_key: b.b_key,
    a_start: num(b.a_start) ?? 0,
    a_end: num(b.a_end),
    b_start: num(b.b_start) ?? 0,
    b_end: num(b.b_end),
    name_a: String(b.name_a || "A").slice(0, 12),
    name_b: String(b.name_b || "B").slice(0, 12),
  };
  await put(`jobs/${id}/meta.json`, meta);
  await put(`jobs/${id}/status.json`, { state: "queued", stage: "prepare", progress: 0 });

  const r = await fetch(process.env.MODAL_SUBMIT_URL!, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.TWINRUN_TOKEN}` },
    body: JSON.stringify({ job_id: id, ...meta }),
  });
  if (!r.ok) {
    await put(`jobs/${id}/status.json`, {
      state: "error",
      error: `Nie udało się uruchomić przetwarzania (${r.status}).`,
    });
    return NextResponse.json({ error: "submit failed" }, { status: 502 });
  }
  return NextResponse.json({ id });
}

export async function GET() {
  const r = await s3().send(new ListObjectsV2Command({ Bucket: BUCKET, Prefix: "jobs/", Delimiter: "/" }));
  const ids = (r.CommonPrefixes ?? [])
    .map((p) => p.Prefix!.split("/")[1])
    .sort()
    .reverse()
    .slice(0, 20);
  const jobs = await Promise.all(
    ids.map(async (id) => {
      const [meta, status] = await Promise.all([getJson(`jobs/${id}/meta.json`), getJson(`jobs/${id}/status.json`)]);
      return meta
        ? { ...meta, state: status?.state ?? "queued", progress: status?.progress ?? 0, result: status?.result ?? null }
        : null;
    }),
  );
  return NextResponse.json({ jobs: jobs.filter(Boolean) }, { headers: { "Cache-Control": "no-store" } });
}
