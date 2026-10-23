const withSeconds = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const withoutSeconds = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** Compact local date and time for table cells, e.g. "Oct 23, 13:47:28". */
export const shortDateTime = (iso: string, seconds = true): string =>
  (seconds ? withSeconds : withoutSeconds).format(new Date(iso));
