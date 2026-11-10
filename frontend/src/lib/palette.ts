// No "use client": the root layout (a server component) imports PALETTE_BOOT_SCRIPT. The functions
// touch document and localStorage, so only call them in the browser.
import { createClient } from "@/lib/supabase/client";

/** Which sentiment colours to show: green/orange/grey/red, or the colour-blind safe set (globals.css). */
export type Palette = "standard" | "colorblind";

export const PALETTE_STORAGE_KEY = "gist-palette";

/**
 * Runs in <head> before the page paints, so a colour-blind user never sees a flash of the standard
 * colours. Kept as a string because it runs before React does.
 */
export const PALETTE_BOOT_SCRIPT = `try{if(localStorage.getItem("${PALETTE_STORAGE_KEY}")==="colorblind")document.documentElement.dataset.palette="colorblind"}catch(e){}`;

const isPalette = (value: unknown): value is Palette => value === "standard" || value === "colorblind";

export function currentPalette(): Palette {
  return document.documentElement.dataset.palette === "colorblind" ? "colorblind" : "standard";
}

/** Shows the palette now and remembers it in this browser for the next page load. */
export function applyPalette(palette: Palette) {
  if (palette === "colorblind") document.documentElement.dataset.palette = "colorblind";
  else delete document.documentElement.dataset.palette;
  try {
    localStorage.setItem(PALETTE_STORAGE_KEY, palette);
  } catch {
    // Storage can be blocked; the palette still applies for this page.
  }
}

/**
 * The palette saved on the account (Supabase user metadata), so it follows the user to another
 * browser. Null when they never chose one.
 */
export async function loadAccountPalette(): Promise<Palette | null> {
  const { data } = await createClient().auth.getSession();
  const saved = data.session?.user.user_metadata?.palette;
  return isPalette(saved) ? saved : null;
}

/** Applies the palette and saves it on the account. */
export async function savePalette(palette: Palette) {
  applyPalette(palette);
  await createClient().auth.updateUser({ data: { palette } });
}
