/* Android Agent adapter. Upstream files alongside this file are unmodified. */
(function () {
  'use strict';
  const states = {
    idle: ['02', '待机'], offline: ['06', '未连接'],
    queued: ['35', '排队中'], sending: ['31', '接收任务'],
    running: ['30', '思考中'], thinking: ['30', '思考中'],
    executing: ['32', '执行中'], searching: ['40', '检索资料'], replying: ['39', '输出回复'],
    awaiting_approval: ['35', '等待审批'], awaiting_input: ['35', '等待输入'],
    paused: ['06', '已暂停'], pause_requested: ['35', '正在暂停'],
    cancel_requested: ['41', '正在停止'], canceled: ['41', '已停止'],
    succeeded: ['33', '任务完成'], failed: ['34', '出错'], interrupted: ['34', '任务中断'],
  };
  const instances = new WeakMap();
  function resolve(status) {
    return typeof status === 'string' && Object.prototype.hasOwnProperty.call(states, status)
      ? states[status] : states.idle;
  }
  function mount(host, options = {}) {
    if (instances.has(host)) return instances.get(host);
    const engine = window.EmotionBall.create(host, { emotion: '02', idle: false, autostart: false, lite: false });
    host.querySelector('svg').setAttribute('aria-hidden', 'true');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let visible = false, enabled = options.active !== false, destroyed = false, suspended = false;
    let active = false, current = '02';
    host.setAttribute('role', 'img');
    function sync() {
      if (destroyed) return;
      const nextActive = enabled && visible && !document.hidden && !motion.matches && !suspended;
      if (nextActive === active) return;
      active = nextActive;
      engine.setActive(active);
      if (!active) engine.renderStatic();
    }
    function suspend() { suspended = true; sync(); }
    function resume() { suspended = false; sync(); }
    const observer = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      sync();
    });
    observer.observe(host);
    document.addEventListener('visibilitychange', sync);
    // Android's older WebView versions expose the original MediaQueryList API.
    if (motion.addEventListener) motion.addEventListener('change', sync);
    else motion.addListener(sync);
    window.addEventListener('pagehide', suspend);
    window.addEventListener('pageshow', resume);
    const api = {
      update(status) {
        if (destroyed) return;
        const [id, label] = resolve(status);
        host.setAttribute('aria-label', `Agent · ${label}`);
        host.title = `Agent · ${label}`;
        host.dataset.emotion = id;
        if (id !== current) { current = id; engine.setEmotion(id); }
      },
      setActive(value) { enabled = Boolean(value); sync(); },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        observer.disconnect();
        document.removeEventListener('visibilitychange', sync);
        if (motion.removeEventListener) motion.removeEventListener('change', sync);
        else motion.removeListener(sync);
        window.removeEventListener('pagehide', suspend);
        window.removeEventListener('pageshow', resume);
        engine.destroy();
        instances.delete(host);
      },
    };
    instances.set(host, api);
    api.update('idle');
    return api;
  }
  window.AgentEmotion = {
    resolve, mount,
    update(id, status) {
      const host = document.getElementById(id);
      if (host) mount(host).update(status);
    },
  };
})();
