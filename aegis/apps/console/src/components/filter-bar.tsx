import Link from "next/link";
import { buttonClass, inputClass, secondaryButtonClass, selectClass } from "@/components/ui";

export interface FilterField {
  name: string;
  label: string;
  value: string;
  kind: "search" | "select";
  placeholder?: string;
  options?: { value: string; label: string }[];
}

/** A plain GET form: the filters live in the URL, are shareable, and run in the database. */
export function FilterBar({ action, fields }: { action: string; fields: FilterField[] }) {
  const active = fields.some((field) => field.value);
  return (
    <form action={action} method="get" className="mb-4 flex flex-wrap items-end gap-3">
      {fields.map((field) =>
        field.kind === "search" ? (
          <label key={field.name} className="block min-w-[11rem] flex-1">
            <span className="sr-only">{field.label}</span>
            <input
              name={field.name}
              defaultValue={field.value}
              placeholder={field.placeholder ?? field.label}
              className={`${inputClass} mt-0`}
            />
          </label>
        ) : (
          <label key={field.name} className="block min-w-[11rem] flex-1">
            <span className="sr-only">{field.label}</span>
            <select name={field.name} defaultValue={field.value} className={`${selectClass} mt-0`}>
              <option value="">{field.placeholder ?? field.label}</option>
              {field.options?.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        )
      )}
      <div className="flex items-center gap-2">
        <button type="submit" className={buttonClass}>
          Apply
        </button>
        {active ? (
          <Link href={action} className={secondaryButtonClass}>
            Clear
          </Link>
        ) : null}
      </div>
    </form>
  );
}
