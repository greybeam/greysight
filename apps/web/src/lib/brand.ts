// Branding is build-time configuration. The hosted SaaS build sets
// NEXT_PUBLIC_BRAND=greybeam to surface the Greybeam company mark alongside the
// product wordmark; OSS self-host leaves it unset and renders the neutral
// "Greysight" wordmark only, so self-hosters are never branded as Greybeam.
const GREYBEAM_BRAND = "greybeam";

export function showBrandLogo(
  brand: string | undefined = process.env.NEXT_PUBLIC_BRAND,
): boolean {
  return brand === GREYBEAM_BRAND;
}
