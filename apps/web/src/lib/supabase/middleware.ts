import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { getSupabasePublicEnv, type Database, type ProductRole, type SessionClaims } from "./types";

const protectedRoutePrefixes = ["/account", "/admin", "/dashboard", "/feedback", "/onboarding", "/projects", "/usage"];
const adminRoutePrefixes = ["/admin"];
const authRedirectRoutePrefixes = ["/login"];
const onboardingExemptRoutePrefixes = ["/account", "/logout", "/onboarding"];

function isRouteMatch(pathname: string, prefixes: string[]) {
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function isAdminRole(role: string | null | undefined): role is Extract<ProductRole, "owner" | "admin"> {
  return role === "owner" || role === "admin";
}

function isAdminClaims(claims: SessionClaims) {
  const appRole = claims.app_metadata?.role;
  const userRole = claims.user_metadata?.role;

  return isAdminRole(typeof appRole === "string" ? appRole : null) || isAdminRole(typeof userRole === "string" ? userRole : null);
}

function redirectToLogin(request: NextRequest, pathname: string) {
  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = "/login";
  redirectUrl.searchParams.set("next", `${pathname}${request.nextUrl.search}`);

  return NextResponse.redirect(redirectUrl);
}

function redirectToDashboard(request: NextRequest) {
  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = "/dashboard";
  redirectUrl.search = "";

  return NextResponse.redirect(redirectUrl);
}

function redirectToOnboarding(request: NextRequest, pathname: string) {
  const redirectUrl = request.nextUrl.clone();
  redirectUrl.pathname = "/onboarding";
  redirectUrl.search = "";
  redirectUrl.searchParams.set("next", `${pathname}${request.nextUrl.search}`);

  return NextResponse.redirect(redirectUrl);
}

function rewriteToForbidden(request: NextRequest) {
  const rewriteUrl = request.nextUrl.clone();
  rewriteUrl.pathname = "/forbidden";
  rewriteUrl.search = "";

  return NextResponse.rewrite(rewriteUrl, { status: 403 });
}

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });
  const { publishableKey, url } = getSupabasePublicEnv();

  const supabase = createServerClient<Database>(url, publishableKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) => {
          request.cookies.set(name, value);
        });

        response = NextResponse.next({ request });

        cookiesToSet.forEach(({ name, options, value }) => {
          response.cookies.set(name, value, options);
        });
      },
    },
  });

  const { data, error } = await supabase.auth.getClaims();
  const claims = error ? null : data?.claims ?? null;
  const pathname = request.nextUrl.pathname;

  if (isRouteMatch(pathname, protectedRoutePrefixes) && !claims) {
    return redirectToLogin(request, pathname);
  }

  if (claims && isRouteMatch(pathname, authRedirectRoutePrefixes)) {
    return redirectToDashboard(request);
  }

  if (claims && isRouteMatch(pathname, protectedRoutePrefixes) && !isRouteMatch(pathname, onboardingExemptRoutePrefixes)) {
    const { data: profile } = await supabase
      .from("product_profiles")
      .select("onboarding_completed")
      .eq("id", claims.sub ?? "")
      .maybeSingle();

    if (!profile?.onboarding_completed) {
      return redirectToOnboarding(request, pathname);
    }
  }

  if (claims && isRouteMatch(pathname, adminRoutePrefixes) && !isAdminClaims(claims)) {
    const { data: adminMembership } = await supabase
      .from("product_memberships")
      .select("role")
      .eq("user_id", claims.sub ?? "")
      .eq("status", "active")
      .in("role", ["owner", "admin"])
      .limit(1)
      .maybeSingle();

    if (!isAdminRole(adminMembership?.role)) {
      return rewriteToForbidden(request);
    }
  }

  return response;
}

export const middlewareConfig = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
