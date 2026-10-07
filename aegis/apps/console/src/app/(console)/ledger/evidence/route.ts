import { apiUrl } from "@/lib/api";
import { readSession } from "@/lib/session";

/** A signed evidence pack (manifest, entries, hashes, signatures), as JSON or NDJSON. */
export async function GET(request: Request): Promise<Response> {
  const session = await readSession();
  if (!session) {
    return new Response("Sign in first.", { status: 401 });
  }
  const format = new URL(request.url).searchParams.get("format") === "ndjson" ? "ndjson" : "json";

  const upstream = await fetch(`${apiUrl}/v1/ledger/evidence?format=${format}`, {
    headers: { Authorization: `Bearer ${session.token}` },
    cache: "no-store"
  });
  if (!upstream.ok || !upstream.body) {
    return new Response("The evidence pack could not be built.", { status: 502 });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": format === "ndjson" ? "application/x-ndjson" : "application/json",
      "Content-Disposition": `attachment; filename="aegis-evidence.${format}"`,
      "Cache-Control": "no-store"
    }
  });
}
