/** Whether Retry is useful for an HTTP/network failure. */
export function isRetryableStatus(status: number | null | undefined): boolean {
  if (status == null) return true;
  if (status === 408 || status === 429) return true;
  return status >= 500;
}
