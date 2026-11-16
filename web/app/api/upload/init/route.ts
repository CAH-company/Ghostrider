import { NextResponse } from "next/server";
import { CreateMultipartUploadCommand } from "@aws-sdk/client-s3";
import { randomUUID } from "node:crypto";
import { BUCKET, s3 } from "@/lib/r2";

export async function POST(req: Request) {
  const { filename, contentType } = await req.json();
  const ext =
    (String(filename ?? "").split(".").pop() || "mov").toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 5) || "mov";
  const key = `uploads/${randomUUID()}/source.${ext}`;
  const r = await s3().send(
    new CreateMultipartUploadCommand({ Bucket: BUCKET, Key: key, ContentType: contentType || "video/quicktime" }),
  );
  return NextResponse.json({ key, uploadId: r.UploadId });
}
