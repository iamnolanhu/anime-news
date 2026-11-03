import type { Metadata } from "next";
import { Studio } from "@/components/studio/studio";

export const metadata: Metadata = {
  title: "Studio",
  description: "Write, edit and render an anime news episode with the Anime News engine running on your own computer.",
};

export default function StudioPage() {
  return <Studio />;
}
