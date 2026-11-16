import { NextResponse } from "next/server";
import { UploadPartCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { BUCKET, s3 } from "@/lib/r2";

export async function POST(req: Request) {
  const { key, uploadId, parts } = (await req.json()) as { key: string; uploadId: string; parts: number[] };
  if (!key?.startsWith("uploads/") || !Array.isArray(parts) || parts.length > 200) {
    return NextResponse.json({ error: "bad request" }, { status: 400 });
  }
  const entries = await Promise.all(
    parts.map(
      async (n) =>
        [
          n,
          await getSignedUrl(
            s3(),
            new UploadPartCommand({ Bucket: BUCKET, Key: key, UploadId: uploadId, PartNumber: n }),
            { expiresIn: 6 * 3600 },
          ),
        ] as const,
    ),
  );
  return NextResponse.json({ urls: Object.fromEntries(entries) });
}
