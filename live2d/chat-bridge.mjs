import {installChatStateBridge} from './state-controller.mjs';
if (['/compact','/bubble'].includes(location.pathname) && !window.localFileChatStateBridgeInstalled) {
  const dispose = installChatStateBridge(document, state => {
    window.webkit?.messageHandlers?.assistantState?.postMessage({state});
  });
  if (dispose) window.localFileChatStateBridgeInstalled = true;
}
