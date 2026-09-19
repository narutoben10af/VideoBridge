(function (root) {
  'use strict';
  function webURL(value) {
    if (typeof value !== 'string' || !value || value.length > 16000 || /[\x00-\x1f\x7f]/.test(value)) return false;
    try { const u = new URL(value); return u.protocol === 'https:' && (!u.port || u.port === '443') && !!u.hostname && !u.username && !u.password; }
    catch { return false; }
  }
  function originPattern(value) { return webURL(value) ? new URL(value).origin + '/*' : null; }
  function mediaResource(url) { return webURL(url) && /\.(m3u8|mp4|m4v|webm)(?:[?#]|$)/i.test(url); }
  function subtitleResource(url) { return webURL(url) && /\.(vtt|srt)(?:[?#]|$)/i.test(url); }
  function displaySource(url) {
    const u = new URL(url);
    return u.hostname + ' · ' + (u.pathname.split('/').filter(Boolean).pop() || 'video').slice(0, 65);
  }
  function subtitleMetadata(url) {
    const filename = new URL(url).pathname.split('/').pop() || '';
    const token = filename.replace(/\.(vtt|srt)$/i, '').replace(/^track_\d+_/i, '').toLowerCase();
    const known = {eng:['English','en'], en:['English','en'], ara:['Arabic','ar'], fre:['French','fr'], fra:['French','fr'], ger:['German','de'], deu:['German','de'], rus:['Russian','ru'], ita:['Italian','it'], brazil_por:['Portuguese (Brazil)','pt-BR'], latin_america_spa:['Spanish (Latin America)','es-419'], spain_spa:['Spanish (Spain)','es-ES'], zho:['Chinese','zh'], chi:['Chinese','zh'], jpn:['Japanese','ja']};
    const match = known[token];
    return match ? {url, label:match[0], language:match[1], inferredLanguage:true} : {url,label:displaySource(url),language:'und'};
  }
  function candidatesFromFrames(frames, pageTitle = '') {
    const candidates = [];
    for (const frame of frames) {
      if (!frame || !webURL(frame.frameURL)) continue;
      const resources = [...new Set((frame.resources || []).filter(mediaResource))];
      for (const video of frame.videos || []) {
        const direct = [...new Set([video.src, ...(video.sources || [])].filter(webURL))];
        const sources = direct.length ? direct : (String(video.src).startsWith('blob:') ? resources : []);
        const tracks = (video.tracks || []).filter(t => webURL(t.url));
        const unmatched = (video.textTracks || []).filter(t => t.mode === 'showing' && !tracks.some(s =>
          (t.language && s.language === t.language) || (t.label && s.label === t.label)));
        for (const url of sources) {
          const key = `${frame.frameId ?? 0}:${video.index}:${url}`;
          const subtitles = tracks.map(t => ({url: t.url, label: t.label || t.language || 'Subtitles', language: t.language || 'und'}));
          const extraTracks = [...new Set((frame.resources || []).filter(subtitleResource))]
            .filter(url => !subtitles.some(t => t.url === url))
            .map(subtitleMetadata);
          candidates.push({key, url, title: pageTitle || frame.title || 'Browser video', referer: frame.frameURL,
            currentTime: Number.isFinite(video.currentTime) ? Math.max(0, video.currentTime) : 0,
            subtitles, extraTracks, unresolvedSubtitles: unmatched.length,
            width: video.width || 0, height: video.height || 0, duration: video.duration,
            playing: video.paused === false, inferred: !direct.length,
            label: `${video.paused === false ? 'Playing' : 'Video'} ${video.index + 1} · ${displaySource(url)}`});
        }
      }
    }
    return candidates;
  }
  function chooseInitial(candidates) {
    // Never silently choose an ad, a previously buffered resource, or one of several renditions.
    return candidates.length === 1 ? candidates[0].key : '';
  }
  function offer(candidate, selectedSubtitles, requestId) {
    if (!candidate || !webURL(candidate.url) || !webURL(candidate.referer)) throw new Error('Choose a playable video first.');
    if (!Array.isArray(selectedSubtitles) || selectedSubtitles.length > 16 || selectedSubtitles.some(t =>
      !webURL(t.url) || typeof t.label !== 'string' || t.label.length > 100 || typeof t.language !== 'string' || (!t.language.length || t.language.length > 35))) {
      throw new Error('Some subtitles could not be sent. Choose up to 16 supported subtitle files.');
    }
    if (candidate.unresolvedSubtitles > 0 && selectedSubtitles.length === 0)
      throw new Error('The captions currently showing have no readable subtitle file. Choose their subtitle file before sending.');
    const message = {protocolVersion: 1, requestId, type: 'offerMedia', payload: {
      url: candidate.url, title: String(candidate.title || 'Browser video').slice(0, 200), referer: candidate.referer,
      currentTime: Math.max(0, Number.isFinite(candidate.currentTime) ? candidate.currentTime : 0),
      subtitles: selectedSubtitles.map(t => ({url:t.url, label:t.label, language:t.language})) }};
    validateOffer(message);
    return message;
  }
  function validateOffer(message) {
    if (!message || message.protocolVersion !== 1 || message.type !== 'offerMedia' ||
        typeof message.requestId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(message.requestId))
      throw new Error('Please update the VideoBridge browser extension.');
    const p = message.payload;
    if (!p || !webURL(p.url) || !webURL(p.referer) || typeof p.title !== 'string' || p.title.length > 200 ||
        !Number.isFinite(p.currentTime) || (p.currentTime < 0 || p.currentTime > 86400) || !Array.isArray(p.subtitles) || p.subtitles.length > 16 ||
        p.subtitles.some(t => !t || !webURL(t.url) || typeof t.label !== 'string' || t.label.length > 100 ||
          typeof t.language !== 'string' || (!t.language.length || t.language.length > 35))) throw new Error('The selected video information is incomplete. Refresh the list and try again.');
    if (new TextEncoder().encode(JSON.stringify(message)).length > 65536) throw new Error('This video has too much subtitle information to send. Select fewer tracks.');
    return message;
  }
  const api = {webURL, originPattern, candidatesFromFrames, chooseInitial, offer, validateOffer, displaySource, subtitleMetadata};
  if (typeof module !== 'undefined') module.exports = api;
  else root.VideoBridgeCore = api;
})(globalThis);
