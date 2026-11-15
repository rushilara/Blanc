// Hosted app path (BLA-61). A public deploy of the frontend serves the landing page
// only: the investigation routes read case evidence from a backend, and the sign-in
// session (BLA-60) only reaches a backend on the same site, so they stay closed there.

/** Routes that open case evidence. */
export const APP_ROUTES = ["/workspace", "/live", "/sample"] as const;

/** Static page shown instead of an app route on a hosted deploy. */
export const HOSTED_NOTICE = "/blanc/hosted.html";

/** True when this deploy is the public hosted landing (``BLANC_HOSTED=1``). */
export function isHosted(env: Record<string, string | undefined>): boolean {
  return env.BLANC_HOSTED === "1" || env.BLANC_HOSTED?.toLowerCase() === "true";
}

/** Where to send ``pathname`` on a hosted deploy, or null to serve it normally. */
export function hostedRedirect(pathname: string, hosted: boolean): string | null {
  if (!hosted) return null;
  const path = pathname.replace(/\/+$/, "") || "/";
  return APP_ROUTES.some(route => path === route || path.startsWith(`${route}/`)) ? HOSTED_NOTICE : null;
}
