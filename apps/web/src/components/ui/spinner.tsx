type SpinnerProps = {
  // Tailwind sizing/color classes for the SVG; defaults to a 1rem square that
  // inherits the surrounding text color via `currentColor`.
  className?: string;
};

// Indeterminate loading spinner. The spin animation is suppressed under
// `prefers-reduced-motion` so it degrades to a static ring for motion-sensitive
// users. Decorative by default (aria-hidden); pair it with visible text.
export default function Spinner({ className = "h-4 w-4" }: SpinnerProps) {
  return (
    <svg
      aria-hidden="true"
      className={`${className} animate-spin motion-reduce:animate-none`}
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z"
        fill="currentColor"
      />
    </svg>
  );
}
