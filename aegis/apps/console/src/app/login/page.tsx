import Image from "next/image";
import { redirect } from "next/navigation";
import { SignInForm } from "@/components/sign-in-form";
import { readSession } from "@/lib/session";

export default async function LoginPage() {
  if (await readSession()) {
    redirect("/");
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <Image
            src="/logo-full.png"
            alt="TechMara"
            width={886}
            height={337}
            priority
            className="mb-6 h-10 w-auto"
          />
          <h1 className="text-2xl font-semibold tracking-tight">Aegis</h1>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            Governed automation for hiring, onboarding and retention.
          </p>
        </div>
        <SignInForm />
        <p className="mt-8 text-xs leading-relaxed text-[var(--color-faint)]">
          Every action an agent takes here is checked against your tenant policy and written to a
          hash-chained ledger. Irreversible actions always stop for a person.
        </p>
      </div>
    </div>
  );
}
