/** Policy reasons arrive with action codes in them ("EXTEND_OFFER is irreversible..."); people read "Extend offer". */
export function humanizeReason(text: string): string {
  const spaced = text.replace(/\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b/g, (code) => code.toLowerCase().replace(/_/g, " "));
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Field names inside prose ("years_experience at 17.00") read as words: "years experience at 17.00". */
export function humanizeFields(text: string): string {
  return text.replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (name) => name.replace(/_/g, " "));
}
