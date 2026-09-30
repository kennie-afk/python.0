import { apiUrl } from "@/lib/api";
import { readSession } from "@/lib/session";

/** Streams the tenant's whole audit trail as CSV, including every hash. */
export async function GET(): Promise<Response> {
  const session = await readSession();
  if (!session) {
    return new Response("Sign in first.", { status: 401 });
  }

  const upstream = await fetch(`${apiUrl}/v1/ledger/export`, {
    headers: { Authorization: `Bearer ${session.token}` },
    cache: "no-store"
  });
  if (!upstream.ok || !upstream.body) {
    return new Response("The audit trail could not be exported.", { status: 502 });
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="aegis-audit-trail.csv"',
      "Cache-Control": "no-store"
    }
  });
}
