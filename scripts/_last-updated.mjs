// Shared helper for the hand-edit scripts (apply-record-fixes, add-county,
// add-announcement-dates): when the script's edit actually changes record bytes,
// stamp projects.json's top-level "last_updated" with today's UTC date.
//
// Editing projects.json as text (rather than JSON.parse → JSON.stringify) matches
// the rest of these scripts and keeps Python's json.dumps byte layout intact.

export function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

const LAST_UPDATED_RE = /^(  "last_updated": ")(\d{4}-\d{2}-\d{2})(",)$/m;

export function stampLastUpdated(text, date) {
  if (!LAST_UPDATED_RE.test(text)) {
    throw new Error('could not find top-level "last_updated" field in projects.json');
  }
  return text.replace(LAST_UPDATED_RE, `$1${date}$3`);
}
