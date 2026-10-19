/**
 * Public-site build (aigenos.io).
 *
 * The Studio is a local tool: it needs the Python backend and *your* model
 * credentials to do anything real. Shipping those surfaces on a public static
 * host would hand visitors empty pickers and failing saves, so the public build
 * shows the product page and the docs, and routes every "build" action to the
 * quickstart instead.
 *
 * Enabled with `VITE_PUBLIC_SITE=1` at build time; off in local dev, so the
 * Studio behaves exactly as before.
 */
export const IS_PUBLIC_SITE = import.meta.env.VITE_PUBLIC_SITE === "1";

/** Where "Start building" points on the public site: install → author → ship. */
export const QUICKSTART_PATH = "/docs/developer-guide";
