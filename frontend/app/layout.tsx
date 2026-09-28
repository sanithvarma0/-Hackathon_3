import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MemoryOps",
  description: "Self-learning production incident commander powered by Hindsight memory",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full">{children}</body>
    </html>
  );
}
