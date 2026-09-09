/** Character/segment counting the way carriers charge it (shared by every
 *  message composer so the numbers never disagree between pages).
 *
 *  GSM-7 messages fit 160 chars per SMS (153 when concatenated); any non-ASCII
 *  character (emoji, curly quotes…) switches the whole message to UCS-2 which
 *  fits 70 (67 concatenated).
 */
export function smsCount(body: string) {
  const unicode = /[^\x00-\x7F]/.test(body);
  const len = body.length;
  const per = unicode ? 70 : 160;
  const perMulti = unicode ? 67 : 153;
  const segments = len === 0 ? 0 : len <= per ? 1 : Math.ceil(len / perMulti);
  return { chars: len, segments, unicode };
}

/** Rough cost label used in composers (~₦4 per segment on Nigerian bundles). */
export function smsCostHint(body: string): string {
  const { chars, segments, unicode } = smsCount(body);
  const parts = [`${chars} chars`, `${segments} SMS${segments === 1 ? "" : "s"}`];
  if (unicode) parts.push("unicode (70/SMS)");
  if (segments > 3) parts.push("long messages cost more");
  return parts.join(" · ");
}
