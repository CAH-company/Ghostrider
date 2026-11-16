import { NextResponse } from "next/server";
import { CompleteMultipartUploadCommand } from "@aws-sdk/client-s3";
import { BUCKET, s3 } from "@/lib/r2";

export async function POST(req: Request) {
  const { key, uploadId, parts } = (await req.json()) as {
    key: string;
    uploadId: string;
    parts: { PartNumber: number; ETag: string }[];
  };
  if (!key?.startsWith("uploads/")) return NextResponse.json({ error: "bad request" }, { status: 400 });
  const sorted = [...parts].sort((a, b) => a.PartNumber - b.PartNumber);
  await s3().send(
    new CompleteMultipartUploadCommand({
      Bucket: BUCKET,
      Key: key,
      UploadId: uploadId,
      MultipartUpload: { Parts: sorted },
    }),
  );
  return NextResponse.json({ ok: true, key });
}
