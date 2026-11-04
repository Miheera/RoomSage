const P = {
  orbit: <><ellipse cx="12" cy="12" rx="10" ry="4.5" /><circle cx="12" cy="12" r="2" /></>,
  walk: <><circle cx="13" cy="4.5" r="2" /><path d="M9 21l2-6-2-3 2-5h4l2 4M11 15l3 2v4" /></>,
  rotate: <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" />,
  home: <path d="M4 12l8-8 8 8M6 10v10h12V10" />,
  expand: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  file: <path d="M6 3h8l4 4v14H6zM14 3v4h4" />,
  camera: <><path d="M4 8h3l2-3h6l2 3h3v12H4z" /><circle cx="12" cy="13.5" r="3.5" /></>,
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  send: <path d="M4 12l16-8-6 16-3-7z" />,
  share: <path d="M12 3v12M8 7l4-4 4 4M5 13v8h14v-8" />,
  download: <path d="M12 4v12M7 11l5 5 5-5M5 20h14" />,
  trash: <path d="M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13" />,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  down: <path d="M12 5v14M6 13l6 6 6-6" />,
  left: <path d="M19 12H5M11 6l-6 6 6 6" />,
  right: <path d="M5 12h14M13 6l6 6-6 6" />,
};

export default function Icon({ name, size = 20 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {P[name]}
    </svg>
  );
}
