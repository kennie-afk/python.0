import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aegis",
  description: "Governed automation for HR"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
