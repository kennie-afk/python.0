import { signOut } from "@/lib/actions";

export function SignOutButton() {
  return (
    <form action={signOut}>
      <button
        type="submit"
        className="w-full cursor-pointer rounded-md px-3 py-2 text-left text-sm text-[var(--color-muted)] transition-colors duration-150 hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
      >
        Sign out
      </button>
    </form>
  );
}
