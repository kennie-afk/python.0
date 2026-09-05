import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sifa",
  description: "Retrieval, ranking and experimentation for personalised feeds"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
