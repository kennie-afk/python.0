import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const tokenCookieName = "aegis_token";
export const tenantCookieName = "aegis_tenant";
export const subjectCookieName = "aegis_subject";

export interface Session {
  token: string;
  tenantId: string;
  subject: string;
}

export async function readSession(): Promise<Session | null> {
  const store = await cookies();
  const token = store.get(tokenCookieName)?.value;
  if (!token) {
    return null;
  }
  return {
    token,
    tenantId: store.get(tenantCookieName)?.value ?? "unknown",
    subject: store.get(subjectCookieName)?.value ?? "unknown"
  };
}

export async function requireSession(): Promise<Session> {
  const session = await readSession();
  if (!session) {
    redirect("/login");
  }
  return session;
}
