import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

export function truncate(str: string, max: number): string {
  if (str.length <= max) return str;
  return str.slice(0, max) + "...";
}

let counter = 0;
export function uniqueId(prefix = "msg"): string {
  return `${prefix}_${Date.now()}_${++counter}`;
}
