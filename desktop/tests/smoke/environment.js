// Child services must never inherit a developer's provider, database, SMTP,
// search credentials or desktop service/profile configuration.
function isolatedSmokeEnv(overrides = {}) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(AGENT_|ANDROID_AGENT_|ANTHROPIC_|DEEPSEEK_|OPENAI_|TAVILY_)/i.test(key)
      && !/^(http_proxy|https_proxy|all_proxy|no_proxy)$/i.test(key)));
  return {
    ...env,
    NO_PROXY: '*', no_proxy: '*',
    HTTP_PROXY: '', HTTPS_PROXY: '', ALL_PROXY: '',
    http_proxy: '', https_proxy: '', all_proxy: '',
    ...overrides,
  };
}

async function latestToolResult(page, name) {
  return page.evaluate(async name => {
    const { job } = await window.AiPanel.client.job(window.AiPanel.getState().currentJobId);
    const results = (job.events || [])
      .filter(event => (event.type || event.event_type) === 'tool_result')
      .map(event => event.payload || event)
      .filter(result => result.name === name);
    return results[results.length - 1] || null;
  }, name);
}

module.exports = { isolatedSmokeEnv, latestToolResult };
