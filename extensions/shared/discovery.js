/* Executed only in the tab the user selected; no listeners or browsing history. */
function inspectVideoFrame() {
  const clean = value => String(value || '').slice(0, 16000);
  const absolute = value => { if (!value) return ''; try { return new URL(value, document.baseURI).href; } catch { return ''; } };
  const videos = [...document.querySelectorAll('video')].slice(0, 30).map((video, index) => {
    const tracks = [...video.querySelectorAll('track')].map(track => ({
      url: absolute(track.getAttribute('src') || ''), label: clean(track.label), language: clean(track.srclang),
      kind: track.kind || 'subtitles', active: track.track?.mode === 'showing'
    })).filter(track => track.url && ['subtitles', 'captions'].includes(track.kind));
    return { index, src: clean(video.currentSrc || video.src),
      sources: [...video.querySelectorAll('source')].map(source => clean(source.src)),
      currentTime: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      duration: Number.isFinite(video.duration) ? video.duration : null,
      width: video.videoWidth || video.clientWidth || 0, height: video.videoHeight || video.clientHeight || 0,
      paused: video.paused, tracks,
      textTracks: [...(video.textTracks || [])].filter(t => ['subtitles', 'captions'].includes(t.kind))
        .map(track => ({label: clean(track.label), language: clean(track.language), mode: track.mode})) };
  });
  return { frameURL: location.href, title: document.title.slice(0, 200), videos,
    resources: performance.getEntriesByType('resource').slice(-1000).map(entry => clean(entry.name))
      .filter(name => /\.(m3u8|mp4|m4v|webm|vtt|srt)(?:[?#]|$)/i.test(name)).slice(-150),
    frames: [...document.querySelectorAll('iframe')].slice(0, 50)
      .map(frame => ({url: absolute(frame.getAttribute('src') || ''), title: clean(frame.title)})) };
}
if (typeof module !== 'undefined') module.exports = { inspectVideoFrame };
