'use strict';
// Native lifecycle state enables animation after the offline page is ready.
window.agentAvatar = window.AgentEmotion.mount(document.getElementById('avatar'), { active: false });
// A page retained in the back/forward cache will resume its existing renderer.
window.addEventListener('pagehide', event => {
  if (!event.persisted) window.agentAvatar.destroy();
});
