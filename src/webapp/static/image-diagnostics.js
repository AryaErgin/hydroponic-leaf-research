// Intentionally excludes filenames, image bytes, account IDs, cookies and tokens.
export function photoDiagnostic(error={},file,stage='prepare',elapsedMs=0,maxBytes){
 const server=error.diagnostic||{},http=Number.isInteger(error.status)?error.status:null;
 const m=String(error.message||'');
 const code=error.code||(/timed out|timeout|AbortError/i.test(m)?stage+'.timeout':/fetch|network|offline|connection/i.test(m)?stage+'.network':http?stage+'.http_'+http:stage+'.failed');
 const result={version:1,timestamp:new Date().toISOString(),stage,code,http_status:http,input_bytes:Number.isFinite(file?.size)?file.size:null,mime_hint:['image/jpeg','image/png','image/heic','image/heif','image/heic-sequence','image/heif-sequence','video/quicktime','video/mp4','application/octet-stream'].includes(file?.type)?file.type:'unknown',elapsed_ms:Math.max(0,Math.round(elapsedMs)),upload_limit_bytes:maxBytes||null};
 const ua=globalThis.navigator?.userAgent||'';result.browser=/CriOS|Chrome/.test(ua)?'Chrome':/FxiOS|Firefox/.test(ua)?'Firefox':/Safari/.test(ua)?'Safari':'Other';const ios=ua.match(/(?:iPhone|CPU) OS ([0-9_]+)/);if(ios)result.ios_version=ios[1].replaceAll('_','.');
 for(const key of ['request_id','detected_format','decoder_error_type','revision'])if(typeof server[key]==='string'&&/^[a-zA-Z0-9_.:-]{1,120}$/.test(server[key]))result[key]=server[key];
 for(const key of ['width','height','frames','received_bytes','pixel_limit'])if(Number.isFinite(server[key]))result[key]=server[key];
 return result;
}
