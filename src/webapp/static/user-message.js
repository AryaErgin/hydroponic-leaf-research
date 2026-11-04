// Keep actionable advice separate from optional diagnostic details.
export function userMessage(error,context=''){
 const text=String(error?.message||error||'Something went wrong.');
 if(/failed to fetch|networkerror|network request|offline|connection/i.test(text))return 'Connection interrupted. Your photo and edits are still here. Check your connection and tap Retry.';
 if(/timed out|timeout|aborterror/i.test(text))return 'This is taking longer than expected. Your work is still here. Tap Retry to check and continue.';
 if(/dimensions|height differs|reference.*mismatch|camera geometry|physical scale|calibration.*payload|recalibrat/i.test(text))return 'Please mark the two outer edges of the pot in this photo to set its scale. Your leaf edits will stay here.';
 if(/identity registry|identity archive|internal instance|invalid.*label|topology/i.test(text))return 'The leaf review could not be saved. Your edits are still here. Save a draft and reopen the review, or retry.';
 if(/native RGB|normal orientation|image format|choose an RGB|corrupt image|heic.*support/i.test(text))return 'This photo could not be opened. Try a JPEG, PNG or HEIC photo, or retake it with your phone camera.';
 if(/HTTP 5\d\d|internal server|service unavailable|temporary.*failure/i.test(text))return context==='calibration'?'Calibration could not be saved. Your points are still here. Tap Retry.':'The server could not finish. Your photo and edits are still here. Tap Retry.';
 if(context==='calibration'&&!/pot|point|diameter|photo|plant|rim|reference|reopen/i.test(text))return 'Calibration could not be saved. Your points are still here. Tap Retry.';
 return text;
}
