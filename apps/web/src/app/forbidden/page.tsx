import type { Metadata } from "next";

import { ForbiddenPage } from "@/components/auth/forbidden-page";

export const metadata: Metadata = {
  title: "Access unavailable - MolCreate",
};

export default function ForbiddenRoute() {
  return <ForbiddenPage />;
}
