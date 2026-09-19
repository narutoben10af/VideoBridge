if (typeof importScripts === 'function') importScripts('core.js');
const api = typeof browser !== 'undefined' ? browser : chrome;
api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== api.runtime.id || sender.url?.split('?')[0] !== api.runtime.getURL('popup.html')) return false;
  (async () => {
    try {
      VideoBridgeCore.validateOffer(message);
      const reply = await api.runtime.sendNativeMessage('app.videobridge.bridge', message);
      if (!reply || reply.protocolVersion !== 1 || reply.requestId !== message.requestId)
        throw new Error('Unexpected app reply');
      sendResponse({ok: reply.type === 'accepted' && typeof reply.payload?.token === 'string', error: reply.type === 'failed' ?
        'VideoBridge could not accept this video. Open the Mac app, check the selection, and try again.' : undefined});
    } catch {
      sendResponse({ok:false, error:'VideoBridge could not receive this video. Open the Mac app and check that the browser connection is installed.'});
    }
  })();
  return true;
});

api.action.onClicked.addListener(async tab => {
  if (Number.isInteger(tab.id)) await api.tabs.create({url:api.runtime.getURL('popup.html') + '?sourceTabId=' + tab.id});
});
