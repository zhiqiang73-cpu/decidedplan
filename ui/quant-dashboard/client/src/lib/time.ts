const SHANGHAI_TZ = "Asia/Shanghai";

function toDate(input: Date | string | number | null | undefined): Date | null {
  if (input === null || input === undefined) return null;
  const d = input instanceof Date ? input : new Date(input);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTimeUTC8(input: Date | string | number | null | undefined): string {
  const d = toDate(input);
  if (!d) return "-";
  return d.toLocaleString("sv-SE", {
    timeZone: SHANGHAI_TZ,
    hour12: false,
  }).replace(",", "");
}

export function formatTimeUTC8(input: Date | string | number | null | undefined): string {
  const text = formatDateTimeUTC8(input);
  return text === "-" ? text : text.slice(11, 19);
}

export function splitDateTimeUTC8(input: Date | string | number | null | undefined): { date: string; time: string } {
  const text = formatDateTimeUTC8(input);
  if (text === "-") return { date: "-", time: "-" };
  return { date: text.slice(0, 10), time: text.slice(11, 19) };
}
