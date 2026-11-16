// Club password -> httpOnly cookie holding a hash derived from the password (works in proxy + route handlers).
export const COOKIE = "tr_auth";

export async function authToken(): Promise<string> {
  const pw = process.env.CLUB_PASSWORD;
  if (!pw) throw new Error("CLUB_PASSWORD is not set");
  const data = new TextEncoder().encode(`twinrun:${pw}`);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function passwordOk(input: string): boolean {
  const pw = process.env.CLUB_PASSWORD;
  if (!pw || input.length !== pw.length) return false;
  let diff = 0;
  for (let i = 0; i < pw.length; i++) diff |= pw.charCodeAt(i) ^ input.charCodeAt(i);
  return diff === 0;
}
