import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

export const BUCKET = process.env.R2_BUCKET!;

let _s3: S3Client | null = null;
export function s3() {
  if (!_s3) {
    _s3 = new S3Client({
      region: "auto",
      endpoint: `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId: process.env.R2_ACCESS_KEY_ID!,
        secretAccessKey: process.env.R2_SECRET_ACCESS_KEY!,
      },
      // R2 does not support the newer checksum headers the SDK adds by default
      requestChecksumCalculation: "WHEN_REQUIRED",
      responseChecksumValidation: "WHEN_REQUIRED",
    });
  }
  return _s3;
}

export const SEVEN_DAYS = 7 * 24 * 3600;

export async function getJson<T = any>(key: string): Promise<T | null> {
  try {
    const r = await s3().send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
    return JSON.parse(await r.Body!.transformToString());
  } catch (e: any) {
    if (e?.name === "NoSuchKey" || e?.$metadata?.httpStatusCode === 404) return null;
    throw e;
  }
}

export function presignGet(key: string, filename?: string) {
  return getSignedUrl(
    s3(),
    new GetObjectCommand({
      Bucket: BUCKET,
      Key: key,
      ResponseContentDisposition: filename ? `attachment; filename="${filename}"` : undefined,
    }),
    { expiresIn: SEVEN_DAYS },
  );
}
