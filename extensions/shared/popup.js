const api = typeof browser !== 'undefined' ? browser : chrome;
const core = VideoBridgeCore;
const $ = id => document.getElementById(id);
let candidates = [], selectedTracks = [], tabId = null;
function selection() { return candidates.find(c => c.key === $('videos').value); }
function updateSelection() {
  const c = selection(); $('tracks').replaceChildren(); selectedTracks = [];
  $('subtitles').hidden = !c; $('send').disabled = !c;
  $('warning').textContent = ''; $('details').textContent = '';
  if (!c) return;
  $('details').textContent = `${c.width} × ${c.height} · ${c.subtitles.length + c.extraTracks.length} subtitle file(s) found`;
  if (c.inferred) $('warning').textContent = 'This video uses a separate stream. Check that the selected source is the episode you want.';
  if (c.unresolvedSubtitles) $('warning').textContent += ' Some captions are visible but their file could not be identified. Choose the matching subtitle file below; otherwise they cannot be transferred.';
  for (const [index, track] of [...c.subtitles, ...c.extraTracks].entries()) {
    const label = document.createElement('label'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = index < c.subtitles.length;
    checkbox.addEventListener('change', () => { selectedTracks = [...$('tracks').querySelectorAll('input')].filter(x => x.checked).map(x => x._track); });
    checkbox._track = track; label.append(checkbox, document.createTextNode(` ${track.label}${track.inferredLanguage ? " · language from filename" : track.language === "und" ? " · language unknown" : ""}`)); $('tracks').append(label);
    if (checkbox.checked) selectedTracks.push(track);
  }
  if (!c.subtitles.length && !c.extraTracks.length) $('tracks').textContent = 'No subtitle files found. Embedded subtitles may be discovered by the Mac app.';
}
async function scan() {
  $('send').disabled = true; $('refresh').disabled = true; $('status').textContent = 'Looking for videos in this tab…';
  $('permissions').replaceChildren();
  try {
    const sourceId = new URLSearchParams(location.search).get('sourceTabId');
    if (!sourceId || !/^\d+$/.test(sourceId)) throw new Error('Open VideoBridge from your video tab.');
    const tab = await api.tabs.get(Number(sourceId));
    if (!Number.isInteger(tab?.id) || !core.webURL(tab.url)) throw new Error('Open a video website, then click VideoBridge again.');
    tabId = tab.id;
    let results;
    try { results = await api.scripting.executeScript({target:{tabId, allFrames:true}, func:inspectVideoFrame}); }
    catch { results = await api.scripting.executeScript({target:{tabId}, func:inspectVideoFrame}); }
    const frames = results.filter(r => r.result).map(r => ({...r.result, frameId:r.frameId}));
    candidates = core.candidatesFromFrames(frames, tab.title || '');
    $('videos').replaceChildren(new Option(candidates.length > 1 ? 'Choose the episode or video to send' : 'Choose a video', ''));
    for (const c of candidates) $('videos').append(new Option(c.label, c.key));
    $('videos').disabled = !candidates.length; $('videos').value = core.chooseInitial(candidates); updateSelection();
    const inspectedOrigins = new Set(frames.map(f => new URL(f.frameURL).origin));
    const wanted = [...new Set(frames.flatMap(f => f.frames || []).map(f => core.originPattern(f.url)).filter(Boolean))];
    for (const origin of wanted) {
      if (inspectedOrigins.has(new URL(origin).origin) || await api.permissions.contains({origins:[origin]})) continue;
      const button = document.createElement('button');
      button.textContent = `Allow video discovery on ${new URL(origin).hostname}`;
      button.title = 'This embedded player needs permission. Only this website will be requested.';
      button.addEventListener('click', async () => {
        try { const granted = await api.permissions.request({origins:[origin]}); if (granted) await scan();
          else $('status').textContent = 'Permission was not granted. You can still choose other videos.'; }
        catch { $('status').textContent = 'Permission could not be requested. Reopen VideoBridge and try again.'; }
      }); $('permissions').append(button);
    }
    $('status').textContent = candidates.length ? `Found ${candidates.length} video source${candidates.length === 1 ? '' : 's'}.` :
      'Start the video, allow its embedded player below if shown, then refresh. Some websites do not expose a transferable video.';
  } catch (error) { $('status').textContent = 'Open a video website, start its player, and try again. Browser settings or permissions may prevent access.'; }
  finally { $('refresh').disabled = false; }
}
$('videos').addEventListener('change', updateSelection);
$('refresh').addEventListener('click', scan);
$('send').addEventListener('click', async () => {
  $('send').disabled = true;
  try {
    const message = core.offer(selection(), selectedTracks, crypto.randomUUID());
    const result = await api.runtime.sendMessage(message);
    if (!result?.ok) throw new Error(result?.error || 'VideoBridge could not open this video. Check the Mac app and try again.');
    $('status').textContent = 'Sent to VideoBridge. Choose Apple TV in the app.';
  } catch (error) { $('status').textContent = error.message || 'Could not send this video. Please try again.'; }
  finally { $('send').disabled = !selection(); }
});
scan();
