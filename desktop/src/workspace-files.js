/* Remote files never pass through Electron's local filesystem bridge. */
((global) => {
  "use strict";

  function remotePath(value, directory = false) {
    const path = String(value || "").replace(/\\/g, "/");
    if (!path || path.startsWith("/") || /^[A-Za-z][A-Za-z\d+.-]*:/.test(path) || path.includes("\0")) {
      throw new Error("远程文件必须使用项目内的相对路径");
    }
    const parts = path.split("/");
    if (parts.includes("..")) throw new Error("远程文件路径不能越出项目");
    const normalized = parts.filter((part) => part && part !== ".").join("/");
    if (!normalized && !directory) throw new Error("请选择文件");
    return normalized || ".";
  }

  function remoteScope(source, project, userId) {
    if (!project?.id || !source.token || !userId) throw new Error("请先连接账号并选择项目");
    const client = new source.constructor();
    client.configure({ baseUrl: source.baseUrl, token: source.token });
    const key = JSON.stringify([client.baseUrl, userId, project.id]);
    const current = () => source.baseUrl === client.baseUrl && source.token === client.token;
    const assertCurrent = () => {
      if (!current()) throw new Error("账号或服务已切换，请重新选择项目");
    };
    return {
      kind: "remote", key, projectId: project.id, userId,
      label: `云端 · ${project.name || project.id}`, current,
      path: remotePath,
      uri(path) { return `agent-remote://${encodeURIComponent(key)}/${remotePath(path).split("/").map(encodeURIComponent).join("/")}`; },
      async list(path = ".") {
        assertCurrent();
        const data = await client.listProjectFiles(project.id, remotePath(path, true));
        assertCurrent();
        return (data.entries || []).map((entry) => ({ ...entry, path: remotePath(entry.path), loaded: entry.type !== "dir" }));
      },
      async search(query = "") {
        assertCurrent();
        const data = await client.searchProjectFiles(project.id, query);
        assertCurrent();
        return data;
      },
      async read(path) {
        assertCurrent();
        const data = await client.readProjectFile(project.id, remotePath(path));
        assertCurrent();
        return { ...data, writable: data.writable !== false && !data.truncated && /^[a-f0-9]{64}$/.test(data.revision || "") };
      },
      async write(path, content, revision) {
        assertCurrent();
        if (!/^[a-f0-9]{64}$/.test(revision || "")) throw new Error("缺少文件版本，请先重新加载文件");
        // Hash the submitted content, not a later GET that could see another writer.
        const hash = await global.crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
        const nextRevision = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
        assertCurrent();
        await client.writeProjectFile(project.id, remotePath(path), content, revision);
        assertCurrent();
        return { revision: nextRevision };
      },
    };
  }

  const exported = { remotePath, remoteScope };
  global.WorkspaceFiles = exported;
  if (typeof module !== "undefined") module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
