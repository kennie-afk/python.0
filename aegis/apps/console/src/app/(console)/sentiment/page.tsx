import { EarlyWarningPanel, SentimentPanel } from "@/components/analytics-panels";
import { PageHeader } from "@/components/ui";

export default function SentimentPage() {
  return (
    <>
      <PageHeader
        title="Sentiment"
        subtitle="How people feel about leadership, pay, balance, tools and growth, reported for groups only. A group too small to hide one person is withheld."
      />
      <div className="space-y-6">
        <SentimentPanel />
        <EarlyWarningPanel />
      </div>
    </>
  );
}
