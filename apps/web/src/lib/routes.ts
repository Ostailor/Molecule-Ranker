import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Boxes,
  CircleDollarSign,
  FlaskConical,
  FolderKanban,
  LayoutDashboard,
  MessageSquareText,
  PackageCheck,
  UserRound,
} from "lucide-react";
import type { ClientSafeProductFeatureFlags } from "@/lib/product/feature-flags";

export type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  feature?: keyof ClientSafeProductFeatureFlags;
};

export const projectId = "project-example-a";
export const runId = "run-example-a";
export const candidateId = "candidate-example-a";

export const productNav: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Projects", href: `/projects/${projectId}`, icon: FolderKanban },
  { label: "New Discovery Run", href: `/projects/${projectId}/runs/new`, icon: FlaskConical, feature: "discoveryRunsPlaceholder" },
  { label: "Result Bundles", href: `/projects/${projectId}/runs/${runId}/result`, icon: PackageCheck, feature: "exportsPlaceholder" },
  { label: "Saved Candidates", href: `/projects/${projectId}/runs/${runId}/candidates`, icon: Boxes },
  { label: "Usage", href: "/usage", icon: CircleDollarSign },
];

export const supportNav: NavItem[] = [
  { label: "Feedback", href: "/feedback", icon: MessageSquareText },
  { label: "Account", href: "/account", icon: UserRound },
];

export const adminNav: NavItem[] = [
  { label: "Admin", href: "/admin", icon: BarChart3, adminOnly: true },
];

export const quickLinks = {
  newProject: "/projects/new",
  newRun: `/projects/${projectId}/runs/new`,
  result: `/projects/${projectId}/runs/${runId}/result`,
  candidate: `/projects/${projectId}/runs/${runId}/candidates/${candidateId}`,
  evidence: `/projects/${projectId}/runs/${runId}/evidence`,
  generated: `/projects/${projectId}/runs/${runId}/generated`,
};

export const allNavItems = [...productNav, ...supportNav, ...adminNav];
