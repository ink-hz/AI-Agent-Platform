import type { MessageTimeStatus } from "./types";


const messageTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Shanghai",
});


export function formatMessageTime(
  value: string | null,
  status: MessageTimeStatus,
): { label: string; dateTime?: string } {
  if (!value || status === "unavailable") return { label: "时间未记录" };
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { label: "时间未记录" };
  const label = messageTimeFormatter.format(date);
  return { label: status === "estimated" ? `约 ${label}` : label, dateTime: value };
}
