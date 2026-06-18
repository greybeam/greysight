// Branding is build-time configuration. This flag gates only the Greybeam logo
// *image* in the header: the hosted build sets NEXT_PUBLIC_BRAND=greybeam to
// render the company mark, while other builds omit the image. The "Greybeam"
// wordmark text itself renders in every build regardless of this flag.
const GREYBEAM_BRAND = "greybeam";

export function showBrandLogo(
  brand: string | undefined = process.env.NEXT_PUBLIC_BRAND,
): boolean {
  return brand === GREYBEAM_BRAND;
}
