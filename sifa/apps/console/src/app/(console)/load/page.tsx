import { LoadTest } from "@/components/load-test";
import { PageHeader } from "@/components/ui";

export default function LoadPage() {
  return (
    <>
      <PageHeader
        title="Load test"
        subtitle="Drive real traffic through the serving path and measure it. Nothing here is cached or pre-computed."
      />
      <LoadTest />
      <p className="mt-6 text-xs leading-relaxed text-[var(--color-faint)]">
        Diversity selection originally dominated this path at sixty five milliseconds a request,
        because it computed a cosine similarity in Python for every candidate pair. The
        similarities are now a single matrix multiply, which is why the median sits in single
        figures.
      </p>
    </>
  );
}
